import SwiftUI

// Lucide list-end (ISC), mirrored to match the user's right-facing queue icon.
// https://lucide.dev/icons/list-end
struct QueueIcon: View {
    var body: some View {
        QueueIconOutline()
            .stroke(style: StrokeStyle(lineWidth: 1.7, lineCap: .round, lineJoin: .round))
            .aspectRatio(1, contentMode: .fit)
            .accessibilityHidden(true)
    }
}

private struct QueueIconOutline: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: 8, y: 5))
        path.addLine(to: CGPoint(x: 21, y: 5))
        path.move(to: CGPoint(x: 8, y: 12))
        path.addLine(to: CGPoint(x: 21, y: 12))
        path.move(to: CGPoint(x: 15, y: 19))
        path.addLine(to: CGPoint(x: 21, y: 19))
        path.move(to: CGPoint(x: 8, y: 16))
        path.addLine(to: CGPoint(x: 11, y: 19))
        path.addLine(to: CGPoint(x: 8, y: 22))
        path.move(to: CGPoint(x: 3, y: 5))
        path.addLine(to: CGPoint(x: 3, y: 17))
        path.addQuadCurve(to: CGPoint(x: 5, y: 19), control: CGPoint(x: 3, y: 19))
        path.addLine(to: CGPoint(x: 11, y: 19))
        return path.applying(CGAffineTransform(scaleX: rect.width / 24, y: rect.height / 24)
            .concatenating(CGAffineTransform(translationX: rect.minX, y: rect.minY)))
    }
}
