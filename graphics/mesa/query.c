/* No drawing: capability query against Mesa's fake DRM device only. */
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include <GLES3/gl31.h>
#include <stdio.h>
int main(void) {
    EGLDisplay d = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    EGLint major, minor, n;
    EGLConfig config;
    const EGLint attrs[] = {EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_ES3_BIT,EGL_NONE};
    const EGLint ctx[] = {EGL_CONTEXT_MAJOR_VERSION,3,EGL_CONTEXT_MINOR_VERSION,1,EGL_NONE};
    if (!eglInitialize(d,&major,&minor) || !eglBindAPI(EGL_OPENGL_ES_API) ||
        !eglChooseConfig(d,attrs,&config,1,&n) || !n) {
        fprintf(stderr,"EGL initialize/config failed: 0x%x\n",eglGetError()); return 1;
    }
    EGLContext c = eglCreateContext(d,config,EGL_NO_CONTEXT,ctx);
    if (c == EGL_NO_CONTEXT || !eglMakeCurrent(d,EGL_NO_SURFACE,EGL_NO_SURFACE,c)) {
        fprintf(stderr,"ES3.1 context failed: 0x%x\n",eglGetError()); return 2;
    }
    printf("NO-HARDWARE QUERY ONLY; no rendering or performance proof\nEGL %d.%d\nEGL extensions: %s\nGL renderer: %s\nGL version: %s\nGL extensions: %s\n",major,minor,eglQueryString(d,EGL_EXTENSIONS),glGetString(GL_RENDERER),glGetString(GL_VERSION),glGetString(GL_EXTENSIONS));
    eglMakeCurrent(d,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
    eglDestroyContext(d,c);
    const EGLint es32[] = {EGL_CONTEXT_MAJOR_VERSION,3,EGL_CONTEXT_MINOR_VERSION,2,EGL_NONE};
    c=eglCreateContext(d,config,EGL_NO_CONTEXT,es32);
    printf("Unmodified ES3.2 request: %s; EGL error 0x%x\n",c==EGL_NO_CONTEXT?"rejected":"accepted",eglGetError());
    if(c!=EGL_NO_CONTEXT) eglDestroyContext(d,c);
    eglTerminate(d);
    return 0;
}
