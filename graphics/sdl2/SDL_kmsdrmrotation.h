/* BaseOS addition: vendor rotation with Mesa GBM window-image origin. */
#include "SDL_hints.h"
static int KMSDRM_Rotation(int width, int height)
{
    const char *hint = SDL_GetHint("SDL_ROTATION");
    int rotation = hint ? SDL_atoi(hint) : (width < height ? 1 : 0);
    return rotation >= 0 && rotation <= 3 ? rotation : 0;
}
static void KMSDRM_RotationUV(int rotation, float x, float y, float uv[2])
{
    float u=x,v=y;
    if(rotation==1) { u=y; v=-x; }
    if(rotation==2) { u=-x; v=-y; }
    if(rotation==3) { u=-y; v=x; }
    /* The imported window BO stores the app's top row at texture t=0. */
    uv[0]=u*0.5f+0.5f; uv[1]=0.5f-v*0.5f;
}
