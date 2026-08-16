import AppKit
import Foundation
import Vision

guard CommandLine.arguments.count == 2 else {
    FileHandle.standardError.write(Data("Usage: swift chart_ocr.swift image.png\n".utf8))
    exit(2)
}

let imagePath = CommandLine.arguments[1]
guard let image = NSImage(contentsOfFile: imagePath) else {
    FileHandle.standardError.write(Data("Image illisible: \(imagePath)\n".utf8))
    exit(3)
}

var proposedRect = NSRect(origin: .zero, size: image.size)
guard let cgImage = image.cgImage(forProposedRect: &proposedRect, context: nil, hints: nil) else {
    FileHandle.standardError.write(Data("Conversion de l'image impossible.\n".utf8))
    exit(4)
}

func recognize(
    level: VNRequestTextRecognitionLevel,
    languages: [String],
    languageCorrection: Bool,
    minimumTextHeight: Float
) throws -> VNRecognizeTextRequest {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = level
    request.recognitionLanguages = languages
    request.usesLanguageCorrection = languageCorrection
    request.minimumTextHeight = minimumTextHeight
    try VNImageRequestHandler(cgImage: cgImage, options: [:]).perform([request])
    return request
}

let request: VNRecognizeTextRequest
do {
    request = try recognize(
        level: .accurate,
        languages: ["fr-FR", "en-US"],
        languageCorrection: true,
        minimumTextHeight: 0.005
    )
} catch {
    // Certaines pages très chargées provoquent un nilError en mode accurate.
    // Le second passage, plus léger, garde l'analyse entièrement locale.
    do {
        request = try recognize(
            level: .fast,
            languages: ["fr-FR"],
            languageCorrection: false,
            minimumTextHeight: 0.008
        )
    } catch {
        FileHandle.standardError.write(Data("Échec OCR: \(error)\n".utf8))
        exit(5)
    }
}

let observations = (request.results ?? []).sorted { first, second in
    let verticalDifference = abs(first.boundingBox.midY - second.boundingBox.midY)
    if verticalDifference > 0.012 {
        return first.boundingBox.midY > second.boundingBox.midY
    }
    return first.boundingBox.minX < second.boundingBox.minX
}

let payload: [[String: Any]] = observations.compactMap { observation in
    guard let candidate = observation.topCandidates(1).first else { return nil }
    let box = observation.boundingBox
    return [
        "text": candidate.string,
        "confidence": Double(candidate.confidence),
        "x": box.minX,
        "y": box.minY,
        "width": box.width,
        "height": box.height,
    ]
}

do {
    let data = try JSONSerialization.data(withJSONObject: payload, options: [])
    FileHandle.standardOutput.write(data)
} catch {
    FileHandle.standardError.write(Data("Sérialisation OCR impossible: \(error)\n".utf8))
    exit(6)
}
