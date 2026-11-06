/* BaseOS addition. Private GLES2 context, standard GBM EGLImage import.
 * Deliberately synchronous GPU handoff: finish producer before import, finish
 * consumer before releasing its source BO. No CPU pixel copies or ION ABI. */
#include "SDL_opengles2.h"
#include "SDL_opengles2_gl2ext.h"
#include <time.h>
#include <stdio.h>
#include "SDL_kmsdrmrotation.h"
#define ROT_GL_FUNCTIONS(X) \
 X(GLuint,CreateShader,(GLenum)) X(void,ShaderSource,(GLuint,GLsizei,const GLchar *const*,const GLint*)) \
 X(void,CompileShader,(GLuint)) X(void,GetShaderiv,(GLuint,GLenum,GLint*)) \
 X(GLuint,CreateProgram,(void)) X(void,AttachShader,(GLuint,GLuint)) \
 X(void,BindAttribLocation,(GLuint,GLuint,const GLchar*)) X(void,LinkProgram,(GLuint)) \
 X(void,GetProgramiv,(GLuint,GLenum,GLint*)) X(void,UseProgram,(GLuint)) \
 X(void,DeleteShader,(GLuint)) X(void,GenTextures,(GLsizei,GLuint*)) \
 X(void,BindTexture,(GLenum,GLuint)) X(void,TexParameteri,(GLenum,GLenum,GLint)) \
 X(void,EGLImageTargetTexture2DOES,(GLenum,GLeglImageOES)) \
 X(void,Viewport,(GLint,GLint,GLsizei,GLsizei)) \
 X(void,VertexAttribPointer,(GLuint,GLint,GLenum,GLboolean,GLsizei,const void*)) \
 X(void,EnableVertexAttribArray,(GLuint)) X(void,DrawArrays,(GLenum,GLint,GLsizei)) \
 X(void,Finish,(void)) X(GLenum,GetError,(void))
typedef struct {
    EGLContext context;
    GLuint program, texture;
#define DECL(ret,n,args) ret (APIENTRY *n) args;
    ROT_GL_FUNCTIONS(DECL)
#undef DECL
    PFNEGLCREATEIMAGEKHRPROC CreateImage;
    PFNEGLDESTROYIMAGEKHRPROC DestroyImage;
} RotationState;
static GLuint RotationShader(RotationState *r, GLenum type, const char *src)
{
    GLint ok; GLuint shader = r->CreateShader(type);
    r->ShaderSource(shader,1,&src,NULL); r->CompileShader(shader);
    r->GetShaderiv(shader,GL_COMPILE_STATUS,&ok);
    if (!ok) { r->DeleteShader(shader); return 0; }
    return shader;
}
static int KMSDRM_RotationPresent(_THIS, SDL_WindowData *w)
{
    RotationState *r = w->rotation_state;
    EGLDisplay d = _this->egl_data->egl_display;
    EGLContext app = (EGLContext)SDL_GL_GetCurrentContext();
    EGLSurface draw = _this->egl_data->eglGetCurrentSurface(EGL_DRAW);
    EGLSurface read = _this->egl_data->eglGetCurrentSurface(EGL_READ);
    struct gbm_bo *bo = NULL; EGLImageKHR image = EGL_NO_IMAGE_KHR;
    int result = -1;
    if (!r) {
        const EGLint attrs[] = { EGL_CONTEXT_CLIENT_VERSION,2,EGL_NONE };
        r = SDL_calloc(1,sizeof(*r)); if (!r) return SDL_OutOfMemory();
        w->rotation_state = r;
#define LOAD(ret,n,args) r->n = (ret (APIENTRY*) args) SDL_EGL_GetProcAddress(_this,"gl" #n); if (!r->n) goto done;
        ROT_GL_FUNCTIONS(LOAD)
#undef LOAD
        r->CreateImage = (PFNEGLCREATEIMAGEKHRPROC)SDL_EGL_GetProcAddress(_this,"eglCreateImageKHR");
        r->DestroyImage = (PFNEGLDESTROYIMAGEKHRPROC)SDL_EGL_GetProcAddress(_this,"eglDestroyImageKHR");
        if (!r->CreateImage || !r->DestroyImage) goto done;
        r->context = _this->egl_data->eglCreateContext(d,_this->egl_data->egl_config,EGL_NO_CONTEXT,attrs);
        if (r->context == EGL_NO_CONTEXT) goto done;
    }
    if (!r->context || !r->Finish) goto done;
    r->Finish(); /* app producer completion, including across unshared contexts */
    if (!_this->egl_data->eglSwapBuffers(d,w->app_surface)) goto done;
    bo = KMSDRM_gbm_surface_lock_front_buffer(w->app_gs); if (!bo) goto done;
    if (!_this->egl_data->eglMakeCurrent(d,w->egl_surface,w->egl_surface,r->context)) goto done;
    if (!r->program) {
        /* Vendor rotation plus the imported GBM window image's top-row origin. */
        const char *vertex="attribute vec2 pos; attribute vec2 texcoord; varying vec2 uv; void main(){uv=texcoord;gl_Position=vec4(pos,0.,1.);}"; GLuint vs,fs; GLint ok;
        const char *fragment = "precision mediump float; varying vec2 uv; uniform sampler2D tex; void main(){gl_FragColor=texture2D(tex,uv);}";
        vs=RotationShader(r,GL_VERTEX_SHADER,vertex); fs=RotationShader(r,GL_FRAGMENT_SHADER,fragment);
        if (!vs || !fs) { if(vs)r->DeleteShader(vs); if(fs)r->DeleteShader(fs); goto done; }
        r->program=r->CreateProgram(); r->AttachShader(r->program,vs); r->AttachShader(r->program,fs);
        r->BindAttribLocation(r->program,0,"pos"); r->BindAttribLocation(r->program,1,"texcoord"); r->LinkProgram(r->program);
        r->DeleteShader(vs); r->DeleteShader(fs); r->GetProgramiv(r->program,GL_LINK_STATUS,&ok);
        if (!ok) goto done;
        r->GenTextures(1,&r->texture); r->BindTexture(GL_TEXTURE_2D,r->texture);
        r->TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_NEAREST);
        r->TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_NEAREST);
        r->TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_CLAMP_TO_EDGE);
        r->TexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE);
    }
    image = r->CreateImage(d,EGL_NO_CONTEXT,EGL_NATIVE_PIXMAP_KHR,(EGLClientBuffer)bo,NULL);
    if (image == EGL_NO_IMAGE_KHR) goto done;
    r->BindTexture(GL_TEXTURE_2D,r->texture);
    r->EGLImageTargetTexture2DOES(GL_TEXTURE_2D,(GLeglImageOES)image);
    {
        const GLfloat vertices[]={-1,-1,1,-1,-1,1,1,1};
        GLfloat uv[8]; int i;
        int width=KMSDRM_gbm_bo_get_width(bo),height=KMSDRM_gbm_bo_get_height(bo);
        for(i=0;i<4;++i) KMSDRM_RotationUV(w->rotation,vertices[2*i],vertices[2*i+1],uv+2*i);
        r->Viewport(0,0,w->rotation&1?height:width,w->rotation&1?width:height);
        r->UseProgram(r->program); r->VertexAttribPointer(0,2,GL_FLOAT,GL_FALSE,0,vertices);
        r->VertexAttribPointer(1,2,GL_FLOAT,GL_FALSE,0,uv); r->EnableVertexAttribArray(1);
        r->EnableVertexAttribArray(0); r->DrawArrays(GL_TRIANGLE_STRIP,0,4);
        r->Finish();
    }
    if (r->GetError()==GL_NO_ERROR && _this->egl_data->eglSwapBuffers(d,w->egl_surface)) result=0;
done:
    if (image!=EGL_NO_IMAGE_KHR) r->DestroyImage(d,image);
    if (bo) KMSDRM_gbm_surface_release_buffer(w->app_gs,bo);
    if (!_this->egl_data->eglMakeCurrent(d,draw,read,app)) result=-1;
    if (result<0) return SDL_SetError("KMSDRM rotation/import failed");
    return 0;
}
void KMSDRM_RotationDestroy(_THIS, SDL_WindowData *w)
{
    RotationState *r=w->rotation_state;
    if(r) {
        if(r->context) _this->egl_data->eglDestroyContext(_this->egl_data->egl_display,r->context);
        SDL_free(r); w->rotation_state=NULL;
    }
    if(w->app_surface) { SDL_EGL_DestroySurface(_this,w->app_surface); w->app_surface=EGL_NO_SURFACE; }
    if(w->app_gs) { KMSDRM_gbm_surface_destroy(w->app_gs); w->app_gs=NULL; }
}
/* Opt-in bounded JSONL. No hashes/readback: timestamps alone cannot prove menu visibility. */
void KMSDRM_Trace(SDL_WindowData *w, const char *event, unsigned frame, unsigned sec, unsigned usec)
{
    const char *dir=SDL_getenv("BASEOS_PRESENT_TRACE");
    struct timespec ts; unsigned long long raw,counter=0; unsigned seq; char path[512]; FILE *fp;
    if (!dir || !*dir || (w->trace_seq > 128 || (w->trace_seq == 128 && SDL_strcmp(event,"entry")==0))) return;
    clock_gettime(CLOCK_MONOTONIC_RAW,&ts); raw=(unsigned long long)ts.tv_sec*1000000000ULL+ts.tv_nsec;
#if defined(__aarch64__)
    __asm__ volatile("mrs %0, cntvct_el0" : "=r"(counter));
#endif
    if (SDL_strcmp(event,"entry")==0) { ++w->trace_seq; }
    seq=w->trace_seq;
    if (seq==128 && SDL_strcmp(event,"entry")!=0) ++w->trace_seq;
    SDL_snprintf(path,sizeof(path),"%s/present.%ld.jsonl",dir,(long)getpid());
    fp=fopen(path,"a"); if(!fp)return;
    fprintf(fp,"{\"event\":\"%s\",\"seq\":%u,\"entry_raw_ns\":%llu,\"raw_ns\":%llu,\"cntvct\":%llu,\"drm_sequence\":%u,\"drm_sec\":%u,\"drm_usec\":%u}\n",event,seq,w->trace_entry,raw,counter,frame,sec,usec);
    fclose(fp);
}
static int KMSDRM_AbandonNext(SDL_WindowData *w)
{
    if(w->next_bo && w->next_bo!=w->bo) KMSDRM_gbm_surface_release_buffer(w->gs,w->next_bo);
    w->next_bo=w->bo; w->bo=NULL;
    return -1;
}
