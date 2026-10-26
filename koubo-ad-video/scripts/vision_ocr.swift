// vision_ocr.swift — macOS 原生 OCR（Vision framework，离线、免费、中文准）
// 编译: swiftc -O -o vision_ocr vision_ocr.swift -framework Vision -framework AppKit
// 用法: vision_ocr 图片.png            -> JSON {width,height,blocks:[{text,conf,x,y,w,h}]}
//      vision_ocr 图片.png --line      -> 按行合并（同一行文字拼成一条，便于整句判断）
import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count >= 2 else {
    FileHandle.standardError.write("usage: vision_ocr <image> [--line]\n".data(using: .utf8)!)
    exit(1)
}
let path = args[1]
let mergeLine = args.contains("--line")

guard let img = NSImage(contentsOfFile: path),
      let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    FileHandle.standardError.write("cannot load image: \(path)\n".data(using: .utf8)!)
    exit(2)
}

let req = VNRecognizeTextRequest()
req.recognitionLevel = .accurate
req.recognitionLanguages = ["zh-Hans", "en-US"]
req.usesLanguageCorrection = false

let handler = VNImageRequestHandler(cgImage: cg, options: [:])
do { try handler.perform([req]) } catch {
    FileHandle.standardError.write("ocr failed: \(error)\n".data(using: .utf8)!)
    exit(3)
}

let W = Double(cg.width), H = Double(cg.height)

struct Box { let text: String; let conf: Double; var x: Double; var y: Double; var w: Double; var h: Double }

var raw: [Box] = []
for obs in req.results ?? [] {
    guard let cand = obs.topCandidates(1).first else { continue }
    let bb = obs.boundingBox                       // 归一化，原点在左下
    let x = bb.minX * W
    let y = (1.0 - bb.maxY) * H                    // 转成左上原点（与图像像素坐标一致）
    raw.append(Box(text: cand.string, conf: Double(cand.confidence),
                   x: x, y: y, w: bb.width * W, h: bb.height * H))
}

var boxes = raw
if mergeLine {
    // 同一行判定：纵向重叠 > 60% 且横向间隙 < 0.6 倍行高 → 合并为一条
    var merged: [Box] = []
    var pool = raw.sorted { $0.y < $1.y }
    while !pool.isEmpty {
        var cur = pool.removeFirst()
        var changed = true
        while changed {
            changed = false
            var rest: [Box] = []
            for b in pool {
                let oy = max(0, min(cur.y + cur.h, b.y + b.h) - max(cur.y, b.y))
                let ov = oy / max(1.0, min(cur.h, b.h))
                let gapX = max(cur.x, b.x) - min(cur.x + cur.w, b.x + b.w)
                if ov > 0.6 && gapX < 0.6 * max(cur.h, b.h) {
                    let x0 = min(cur.x, b.x), y0 = min(cur.y, b.y)
                    let x1 = max(cur.x + cur.w, b.x + b.w), y1 = max(cur.y + cur.h, b.y + b.h)
                    let left = cur.x <= b.x ? cur : b
                    let right = cur.x <= b.x ? b : cur
                    cur = Box(text: left.text + right.text, conf: min(cur.conf, b.conf),
                              x: x0, y: y0, w: x1 - x0, h: y1 - y0)
                    changed = true
                } else {
                    rest.append(b)
                }
            }
            pool = rest
        }
        merged.append(cur)
    }
    boxes = merged.sorted { $0.y < $1.y }
}

var out: [[String: Any]] = []
for b in boxes {
    out.append(["text": b.text, "conf": (b.conf * 1000).rounded() / 1000,
                "x": Int(b.x.rounded()), "y": Int(b.y.rounded()),
                "w": Int(b.w.rounded()), "h": Int(b.h.rounded())])
}
let payload: [String: Any] = ["width": Int(W), "height": Int(H), "count": out.count, "blocks": out]
let data = try JSONSerialization.data(withJSONObject: payload,
                                      options: [.prettyPrinted, .withoutEscapingSlashes])
print(String(data: data, encoding: .utf8)!)
