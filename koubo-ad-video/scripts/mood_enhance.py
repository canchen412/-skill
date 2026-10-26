#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
⚠️⚠️ 已否决 —— 灿爹 2026-10-07 审美否决：「你这个氛围感增强，看起来不好看。不用这个，还是用原来的吧。」
原因：S曲线+局部增强把图推成「假 HDR / 塑料感」，与本司 2D 厚涂动漫画风冲突。
保留仅作技术参考，**不要在出片流程里调用**。
正解走源头：生图阶段用「张力场景 + 硬光影 + 可动粒子」prompt
（见 references/张力场景库与i2v友好生图规范.md），本脚本不参与渲染。
───────────────────────────────
原说明（已失效）：
氛围感增强（零积分·纯后期）：把「灰调棚拍死片」救成「有光影层次」
链路：白点拉伸→S曲线对比→CLAHE 局部对比→Bloom 辉光→冷暖分离→暗角→颗粒
"""
import cv2, numpy as np

def _u8(x): return np.clip(x,0,255).astype(np.uint8)

def enhance(im, strength=1.0):
    f = im.astype(np.float32)/255.0
    # ① 白点拉伸：把最亮点从 ~0.6 拉到 ~0.92（关键！造出真实高光）
    p5,p95 = np.percentile(f,5), np.percentile(f,99.5)
    wp = p5 + (1.0-p5)*0.92
    f = np.clip((f-p5)/(max(wp-p5,1e-4)),0,1)
    # ② S 曲线对比（压暗+提亮拉开）
    s = np.clip((f-0.5)*1.42+0.5,0,1); f = f*(1-strength*0.75)+s*(strength*0.75)
    # ③ 黑位下压但保留细节
    f = np.clip((f-0.035)*1.10,0,1)
    # ④ CLAHE 局部对比（立体感）—— 只对 L 通道做（CLAHE 不吃 3 通道）
    lab = cv2.cvtColor(_u8(f*255),cv2.COLOR_BGR2LAB)
    cl = cv2.createCLAHE(clipLimit=2.2,tileGridSize=(8,8)).apply(lab[:,:,0])
    lab[:,:,0] = cl
    f = cv2.cvtColor(lab,cv2.COLOR_LAB2BGR).astype(np.float32)/255.0
    f = f*(1-strength*0.45)+(np.clip((f-0.5)*1.18+0.5,0,1))*(strength*0.45)
    # ⑤ Bloom 辉光（亮部扩散 = 氛围光）
    h,w = f.shape[:2]
    lum0 = f.mean(axis=2, keepdims=True)                    # 亮度掩膜（3 通道要同形）
    mask = np.clip((lum0-0.68)/0.32, 0, 1)
    bright = mask * f
    blur = cv2.GaussianBlur(bright,(0,0),max(w,h)*0.012)
    bloom = cv2.GaussianBlur(bright,(0,0),max(w,h)*0.045)
    f = f + (blur*0.55+bloom*0.95)*strength
    # ⑥ 冷暖分离：暗部推青蓝、亮部推琥珀（电影感）
    lum = f.mean(axis=2,keepdims=True)                       # (H,W,1)
    shadow_t = np.clip(1.0-lum/0.55,0,1)                     # (H,W,1)
    high_t   = np.clip((lum-0.62)/0.38,0,1)                  # (H,W,1)
    f = f + shadow_t*np.array([-0.035,0.0,0.075])*strength + high_t*np.array([0.06,0.028,-0.045])*strength
    # ⑦ 暗角
    yy,xx = np.mgrid[0:h,0:w]
    r = np.sqrt(((xx-w/2)/(w/2))**2+((yy-h/2)/(h/2))**2)
    f = f*np.clip(1.0-np.clip((r-0.62)/0.85,0,1)**1.7*0.68,0.35,1)[...,None]
    f = np.clip(f,0,1)
    # ⑧ 胶片颗粒
    rng = np.random.default_rng(7)
    g = rng.normal(0,0.016*strength,(h,w,1)).astype(np.float32)
    f = np.clip(f+g,0,1)
    return _u8(f*255)

if __name__=="__main__":
    import sys, os
    src=sys.argv[1]; dst=sys.argv[2]
    st=float(sys.argv[3]) if len(sys.argv)>3 else 1.0
    im=cv2.imread(src)
    assert im is not None, src
    out=enhance(im,st)
    cv2.imwrite(dst,out,[cv2.IMWRITE_PNG_COMPRESSION,2])
    g1=cv2.cvtColor(im,cv2.COLOR_BGR2GRAY); g2=cv2.cvtColor(out,cv2.COLOR_BGR2GRAY)
    for tag,g in (("原图",g1),("增强",g2)):
        h=np.histogram(g,bins=256,range=(0,256))[0].astype(np.float32); h/=h.sum()
        nz=np.where(h>0.002)[0]
        print(f"  {tag}: 暗部{h[:45].sum()*100:5.2f}% 亮部{h[205:].sum()*100:5.2f}% "
              f"P5={np.percentile(g,5):5.1f} P95={np.percentile(g,95):5.1f} 跨度{nz.min()}-{nz.max()}")
