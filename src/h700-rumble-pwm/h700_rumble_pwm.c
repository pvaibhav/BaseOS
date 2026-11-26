// SPDX-License-Identifier: GPL-2.0
/* Software PWM wrapper for H700 rumble across sysfs `moto` and evdev FF_RUMBLE.
 *
 * Stock H700 kernels expose two rumble paths that both drive the ERM motor at
 * full battery voltage: /sys/class/power_supply/axp2202-battery/moto (used by
 * NextUI and minarch) and /dev/input/event* "ANBERNIC-keys" EV_FF (used by
 * standalone SDL2 / PortMaster ports). Wrapping both paths in one 50 Hz carrier
 * caps boolean rumble at max_duty_pct and maps graded FF_RUMBLE magnitudes into
 * the usable [min_duty_pct, max_duty_pct] motor range.
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/device.h>
#include <linux/power_supply.h>
#include <linux/sysfs.h>
#include <linux/kernfs.h>
#include <linux/hrtimer.h>
#include <linux/workqueue.h>
#include <linux/spinlock.h>
#include <linux/mutex.h>
#include <linux/ktime.h>
#include <linux/math64.h>
#include <linux/suspend.h>
#include <linux/input.h>
#include <linux/slab.h>

#define RUMBLE_PSY_NAME             "axp2202-battery"
#define RUMBLE_ATTR_NAME            "moto"
#define RUMBLE_INPUT_NAME           "ANBERNIC-keys"
#define RUMBLE_DEFAULT_MAX_DUTY_PCT 60
#define RUMBLE_DEFAULT_MIN_DUTY_PCT 40
#define RUMBLE_PERIOD_NS            (20ULL * NSEC_PER_MSEC)
#define RUMBLE_MIN_PULSE_NS         (1ULL * NSEC_PER_MSEC)
#define RUMBLE_FF_MAX_MAGNITUDE     0xFFFFU

static int max_duty_pct = RUMBLE_DEFAULT_MAX_DUTY_PCT;
module_param(max_duty_pct, int, 0644);
MODULE_PARM_DESC(max_duty_pct, "Maximum rumble duty cycle percent (0-100, default 60)");

static int min_duty_pct = RUMBLE_DEFAULT_MIN_DUTY_PCT;
module_param(min_duty_pct, int, 0644);
MODULE_PARM_DESC(min_duty_pct, "Minimum active rumble duty cycle percent (0-100, default 40)");

static struct power_supply *rumble_psy;
static struct device_attribute *orig_dev_attr;
static ssize_t (*orig_store)(struct device *dev, struct device_attribute *attr,
			     const char *buf, size_t count);
static struct device_attribute rumble_moto_attr;

static struct input_dev *hooked_input_dev;
static int (*orig_ff_upload)(struct input_dev *dev, struct ff_effect *effect,
			     struct ff_effect *old);
static int (*orig_ff_playback)(struct input_dev *dev, int effect_id, int value);
static int ff_playing_id = -1;

static DEFINE_SPINLOCK(ff_lock);
static DEFINE_SPINLOCK(state_lock);
static DEFINE_MUTEX(hw_mutex);
static struct hrtimer pwm_timer;
static struct work_struct rumble_work;
static struct delayed_work ff_stop_work;

static bool pwm_active, phase_on;
static int target_duty_pct, wanted_hw, current_hw = -1;
static u64 pwm_on_ns, pwm_off_ns;

static int effective_min_duty(void)
{
	return clamp(min_duty_pct, 0, 100);
}

static int effective_max_duty(void)
{
	int cap = clamp(max_duty_pct, 0, 100);
	int floor = effective_min_duty();

	if (cap > 0 && cap < floor)
		return floor;
	return cap;
}

static int scale_ff_effect(const struct ff_effect *eff)
{
	int cap = effective_max_duty();
	int floor = effective_min_duty();
	u32 mag = 0;

	if (cap == 0)
		return 0;

	switch (eff->type) {
	case FF_RUMBLE:
		mag = max(eff->u.rumble.strong_magnitude,
			  eff->u.rumble.weak_magnitude);
		break;
	case FF_CONSTANT:
		mag = min_t(u32, (u32)abs(eff->u.constant.level) * 2U,
			    RUMBLE_FF_MAX_MAGNITUDE);
		break;
	case FF_PERIODIC:
		mag = min_t(u32, (u32)abs(eff->u.periodic.magnitude) * 2U,
			    RUMBLE_FF_MAX_MAGNITUDE);
		break;
	default:
		return cap;
	}

	if (mag == 0)
		return 0;
	if (cap <= floor)
		return cap;

	return floor + (int)div_u64((u64)mag * (u64)(cap - floor),
				    RUMBLE_FF_MAX_MAGNITUDE);
}

static void rumble_compute_pulses(int duty_pct, u64 *out_on_ns, u64 *out_off_ns)
{
	u64 on_ns = div_u64(RUMBLE_PERIOD_NS * (u64)duty_pct, 100);

	on_ns = clamp_t(u64, on_ns, RUMBLE_MIN_PULSE_NS,
			RUMBLE_PERIOD_NS - RUMBLE_MIN_PULSE_NS);
	*out_on_ns = on_ns;
	*out_off_ns = RUMBLE_PERIOD_NS - on_ns;
}

static void rumble_write_hw(int hw)
{
	const char *cmd = hw ? "1\n" : "0\n";

	mutex_lock(&hw_mutex);
	if (hw != current_hw && orig_store && rumble_psy &&
	    orig_store(&rumble_psy->dev, orig_dev_attr, cmd, 2) >= 0)
		current_hw = hw;
	mutex_unlock(&hw_mutex);
}

static void rumble_work_fn(struct work_struct *work)
{
	unsigned long flags;
	int hw;

	spin_lock_irqsave(&state_lock, flags);
	hw = wanted_hw;
	spin_unlock_irqrestore(&state_lock, flags);
	rumble_write_hw(hw);
}

static enum hrtimer_restart rumble_timer_fn(struct hrtimer *timer)
{
	unsigned long flags;

	spin_lock_irqsave(&state_lock, flags);
	if (!pwm_active) {
		spin_unlock_irqrestore(&state_lock, flags);
		return HRTIMER_NORESTART;
	}
	phase_on = !phase_on;
	wanted_hw = phase_on ? 1 : 0;
	hrtimer_forward_now(timer, ns_to_ktime(phase_on ? pwm_on_ns : pwm_off_ns));
	queue_work(system_highpri_wq, &rumble_work);
	spin_unlock_irqrestore(&state_lock, flags);
	return HRTIMER_RESTART;
}

static void rumble_set_duty(int duty_pct)
{
	unsigned long flags;
	u64 on_ns = 0, off_ns = 0;

	if (duty_pct > 0 && duty_pct < 100)
		rumble_compute_pulses(duty_pct, &on_ns, &off_ns);

	spin_lock_irqsave(&state_lock, flags);
	target_duty_pct = duty_pct;
	if (duty_pct <= 0 || duty_pct >= 100) {
		pwm_active = false;
		wanted_hw = (duty_pct >= 100) ? 1 : 0;
		hrtimer_try_to_cancel(&pwm_timer);
		queue_work(system_highpri_wq, &rumble_work);
	} else {
		pwm_on_ns = on_ns;
		pwm_off_ns = off_ns;
		if (!pwm_active) {
			pwm_active = true;
			phase_on = true;
			wanted_hw = 1;
			queue_work(system_highpri_wq, &rumble_work);
			hrtimer_start(&pwm_timer, ns_to_ktime(on_ns), HRTIMER_MODE_REL);
		}
	}
	spin_unlock_irqrestore(&state_lock, flags);
}

static void rumble_stop_sync(void)
{
	rumble_set_duty(0);
	hrtimer_cancel(&pwm_timer);
	cancel_work_sync(&rumble_work);
	rumble_write_hw(0);
}

static ssize_t rumble_moto_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	int duty;

	spin_lock_irqsave(&state_lock, flags);
	duty = target_duty_pct;
	spin_unlock_irqrestore(&state_lock, flags);
	return scnprintf(buf, PAGE_SIZE, "%d\n", duty > 0 ? 1 : 0);
}

static ssize_t rumble_moto_store(struct device *dev, struct device_attribute *attr,
				 const char *buf, size_t count)
{
	int val;

	if (kstrtoint(buf, 10, &val) != 0)
		return -EINVAL;
	rumble_set_duty(val > 0 ? effective_max_duty() : 0);
	return count;
}

static int rumble_pm_notify(struct notifier_block *nb, unsigned long event, void *unused)
{
	if (event == PM_SUSPEND_PREPARE || event == PM_HIBERNATION_PREPARE)
		rumble_stop_sync();
	return NOTIFY_DONE;
}

static struct notifier_block rumble_pm_nb = { .notifier_call = rumble_pm_notify };

static void rumble_ff_apply_locked(const struct ff_effect *eff, int effect_id)
{
	int duty_pct = scale_ff_effect(eff);

	rumble_set_duty(duty_pct);
	if (duty_pct <= 0) {
		ff_playing_id = -1;
		cancel_delayed_work(&ff_stop_work);
		return;
	}
	ff_playing_id = effect_id;
	if (eff->replay.length > 0)
		mod_delayed_work(system_highpri_wq, &ff_stop_work,
				 msecs_to_jiffies(eff->replay.length));
	else
		cancel_delayed_work(&ff_stop_work);
}

static void rumble_ff_stop_work_fn(struct work_struct *work)
{
	unsigned long flags;

	spin_lock_irqsave(&ff_lock, flags);
	if (ff_playing_id >= 0) {
		ff_playing_id = -1;
		rumble_set_duty(0);
	}
	spin_unlock_irqrestore(&ff_lock, flags);
}

static int rumble_ff_upload(struct input_dev *dev, struct ff_effect *effect,
			    struct ff_effect *old)
{
	unsigned long flags;
	int rc = orig_ff_upload ? orig_ff_upload(dev, effect, old) : 0;

	if (rc < 0)
		return rc;
	spin_lock_irqsave(&ff_lock, flags);
	if (effect->id == ff_playing_id)
		rumble_ff_apply_locked(effect, effect->id);
	spin_unlock_irqrestore(&ff_lock, flags);
	return 0;
}

static int rumble_ff_playback(struct input_dev *dev, int effect_id, int value)
{
	unsigned long flags;

	if (!dev->ff || effect_id < 0 || effect_id >= dev->ff->max_effects)
		return -EINVAL;

	spin_lock_irqsave(&ff_lock, flags);
	if (value <= 0) {
		ff_playing_id = -1;
		cancel_delayed_work(&ff_stop_work);
		rumble_set_duty(0);
	} else {
		rumble_ff_apply_locked(&dev->ff->effects[effect_id], effect_id);
	}
	spin_unlock_irqrestore(&ff_lock, flags);
	return 0;
}

static int rumble_input_connect(struct input_handler *handler,
				struct input_dev *dev,
				const struct input_device_id *id)
{
	struct input_handle *handle;
	int err;

	if (!dev->name || strcmp(dev->name, RUMBLE_INPUT_NAME) != 0 || !dev->ff)
		return -ENODEV;
	if (hooked_input_dev)
		return -EBUSY;

	handle = kzalloc(sizeof(*handle), GFP_KERNEL);
	if (!handle)
		return -ENOMEM;
	handle->dev = dev;
	handle->handler = handler;
	handle->name = "h700_rumble_pwm";

	err = input_register_handle(handle);
	if (err) {
		kfree(handle);
		return err;
	}
	hooked_input_dev = dev;
	orig_ff_upload = dev->ff->upload;
	orig_ff_playback = dev->ff->playback;
	dev->ff->upload = rumble_ff_upload;
	dev->ff->playback = rumble_ff_playback;
	pr_info("h700_rumble_pwm: hooked evdev FF on %s\n", dev->name);
	return 0;
}

static void rumble_input_disconnect(struct input_handle *handle)
{
	struct input_dev *dev = handle->dev;
	unsigned long flags;

	if (dev == hooked_input_dev && dev->ff) {
		dev->ff->upload = orig_ff_upload;
		dev->ff->playback = orig_ff_playback;
		hooked_input_dev = NULL;
	}
	spin_lock_irqsave(&ff_lock, flags);
	ff_playing_id = -1;
	spin_unlock_irqrestore(&ff_lock, flags);
	cancel_delayed_work_sync(&ff_stop_work);
	input_unregister_handle(handle);
	kfree(handle);
	rumble_stop_sync();
}

static const struct input_device_id rumble_input_ids[] = {
	{ .flags = INPUT_DEVICE_ID_MATCH_EVBIT, .evbit = { BIT_MASK(EV_FF) } },
	{ },
};

static struct input_handler rumble_input_handler = {
	.connect = rumble_input_connect,
	.disconnect = rumble_input_disconnect,
	.name = "h700_rumble_pwm",
	.id_table = rumble_input_ids,
};

static int __init h700_rumble_pwm_init(void)
{
	struct kernfs_node *kn;
	const struct attribute *orig_attr;
	umode_t orig_mode;
	int err;

	rumble_psy = power_supply_get_by_name(RUMBLE_PSY_NAME);
	if (!rumble_psy) {
		pr_err("h700_rumble_pwm: power_supply %s not found\n", RUMBLE_PSY_NAME);
		return -ENODEV;
	}

	kn = kernfs_find_and_get(rumble_psy->dev.kobj.sd, RUMBLE_ATTR_NAME);
	if (!kn || !kn->priv) {
		if (kn)
			kernfs_put(kn);
		power_supply_put(rumble_psy);
		rumble_psy = NULL;
		pr_err("h700_rumble_pwm: %s attribute not found\n", RUMBLE_ATTR_NAME);
		return -ENOENT;
	}

	orig_attr = (const struct attribute *)kn->priv;
	orig_dev_attr = container_of(orig_attr, struct device_attribute, attr);
	orig_mode = (kn->mode & 0777) ? (kn->mode & 0777) : 0644;
	orig_store = orig_dev_attr->store;
	kernfs_put(kn);

	if (!orig_store) {
		power_supply_put(rumble_psy);
		rumble_psy = NULL;
		pr_err("h700_rumble_pwm: %s has no store handler\n", RUMBLE_ATTR_NAME);
		return -EINVAL;
	}

	hrtimer_init(&pwm_timer, CLOCK_MONOTONIC, HRTIMER_MODE_REL);
	pwm_timer.function = rumble_timer_fn;
	INIT_WORK(&rumble_work, rumble_work_fn);
	INIT_DELAYED_WORK(&ff_stop_work, rumble_ff_stop_work_fn);

	sysfs_attr_init(&rumble_moto_attr.attr);
	rumble_moto_attr.attr.name = RUMBLE_ATTR_NAME;
	rumble_moto_attr.attr.mode = orig_mode;
	rumble_moto_attr.show = rumble_moto_show;
	rumble_moto_attr.store = rumble_moto_store;

	device_remove_file(&rumble_psy->dev, orig_dev_attr);
	err = device_create_file(&rumble_psy->dev, &rumble_moto_attr);
	if (err) {
		if (device_create_file(&rumble_psy->dev, orig_dev_attr) == 0 &&
		    sysfs_chmod_file(&rumble_psy->dev.kobj, &orig_dev_attr->attr, orig_mode) != 0)
			pr_warn("h700_rumble_pwm: failed to restore %s mode\n", RUMBLE_ATTR_NAME);
		power_supply_put(rumble_psy);
		rumble_psy = NULL;
		pr_err("h700_rumble_pwm: failed to install %s wrapper (%d)\n",
		       RUMBLE_ATTR_NAME, err);
		return err;
	}

	register_pm_notifier(&rumble_pm_nb);
	err = input_register_handler(&rumble_input_handler);
	if (err)
		pr_warn("h700_rumble_pwm: failed to register input handler (%d)\n", err);

	pr_info("h700_rumble_pwm: attached to %s/%s (min_duty_pct=%d, max_duty_pct=%d)\n",
		RUMBLE_PSY_NAME, RUMBLE_ATTR_NAME,
		effective_min_duty(), effective_max_duty());
	return 0;
}

/* Hooks permanent built-in power_supply and input devices; reboot to undo. */
module_init(h700_rumble_pwm_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Software PWM wrapper for H700 rumble");
