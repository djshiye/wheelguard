import QtQuick
import QtQuick.Shapes

// A stylised top-down drawing of a supported mouse (nose up), in the app's colours. Original artwork:
// plain shapes only, no logos. Draws on a 120 x 200 grid, scaled by `unit`.
//   modelKey   models.Model.key of the connected mouse ("" draws a neutral mouse)
//   highlight  button to light up: 0 left, 1 right, 2 middle, 3 back (side), 4 forward (side); -1 none
//   dim        draw it faded (mouse asleep or not read yet)
Item {
    id: root
    property string modelKey: ""
    property int highlight: -1
    property bool dim: false
    property real unit: 1

    width: 120 * unit
    height: 200 * unit
    opacity: dim ? 0.45 : 1
    Behavior on opacity { NumberAnimation { duration: Theme.normal } }

    Accessible.role: Accessible.Graphic
    Accessible.name: (root.modelKey === "logitech-pro-x-superlight" ? "Logitech G Pro X Superlight"
                      : (root.modelKey === "razer-deathadder-v4-pro" ? "Razer DeathAdder V4 Pro"
                         : (root.modelKey === "endgame-xm2w-4k-v2" ? "Endgame Gear XM2w 4k v2" : "Mouse")))
                     + ", top view" + (root.highlight >= 0 ? ", highlighting the "
                         + ["left button", "right button", "middle click", "back button", "forward button"][root.highlight] : "")

    // Per model: the left and right main buttons (split by the seam) and the palm area behind them.
    readonly property var shapes: ({
        "endgame-xm2w-4k-v2": {
            left: "M60 6 C40 6 24 18 21 46 C19.5 62 19 74 19.5 84 L60 84 Z",
            right: "M60 6 C80 6 96 18 99 46 C100.5 62 101 74 100.5 84 L60 84 Z",
            palm: "M19.5 84 L100.5 84 C101 110 99 140 95 160 C89 184 76 194 60 194 C44 194 31 184 25 160 C21 140 19 110 19.5 84 Z",
            wheel: Qt.rect(55, 28, 10, 28), side: [Qt.rect(13.5, 94, 7, 26), Qt.rect(13.5, 58, 7, 24)]
        },
        "logitech-pro-x-superlight": {
            left: "M60 7 C42 7 28 18 25.5 44 C24 60 23.5 72 24 80 L60 80 Z",
            right: "M60 7 C78 7 92 18 94.5 44 C96 60 96.5 72 96 80 L60 80 Z",
            palm: "M24 80 L96 80 C97 108 95 138 91 158 C86 182 74 193 60 193 C46 193 34 182 29 158 C25 138 23 108 24 80 Z",
            wheel: Qt.rect(55.5, 26, 9, 26), side: [Qt.rect(18, 90, 6.5, 24), Qt.rect(18, 56, 6.5, 22)]
        },
        "razer-deathadder-v4-pro": {
            left: "M58 6 C38 6 26 18 24 46 C23 60 22.5 72 23 82 L58 82 Z",
            right: "M58 6 C80 6 97 18 101 46 C103 62 104 74 103.5 82 L58 82 Z",
            palm: "M23 82 L103.5 82 C106 108 104 138 98 162 C91 184 76 194 58 194 C40 194 30 182 26 160 C22 140 21 108 23 82 Z",
            wheel: Qt.rect(53, 28, 10, 28), side: [Qt.rect(17, 96, 6.5, 26), Qt.rect(17, 60, 6.5, 24)]
        }
    })
    readonly property var spec: shapes[modelKey] || shapes["endgame-xm2w-4k-v2"]
    readonly property color bodyEdge: Theme.dark ? Qt.rgba(1, 1, 1, 0.34) : Qt.rgba(0, 0, 0, 0.34)
    readonly property color buttonFill: Theme.dark ? Theme.track : Theme.card
    readonly property color palmTop: Theme.dark ? Theme.track : Theme.card
    readonly property color palmBottom: Theme.dark ? Theme.cardHi : Theme.inset
    readonly property color lit: Theme.alpha(Theme.accentB, Theme.dark ? 0.45 : 0.40)

    Item {
        width: 120
        height: 200
        scale: root.unit
        transformOrigin: Item.TopLeft

        Shape {
            anchors.fill: parent
            // Only at 1x: at fractional and 2x display scales the layer renders clipped or blank (seen with
            // QT_SCALE_FACTOR 1.25 and 2), and high-DPI pixels give smooth enough edges on their own.
            layer.enabled: Screen.devicePixelRatio === 1
            layer.samples: 4
            preferredRendererType: Shape.GeometryRenderer

            ShapePath {     // palm: a soft top-to-bottom gradient
                strokeColor: root.bodyEdge
                strokeWidth: 1.6
                joinStyle: ShapePath.RoundJoin
                fillGradient: LinearGradient {
                    x1: 0; y1: 80; x2: 0; y2: 194
                    GradientStop { position: 0; color: root.palmTop }
                    GradientStop { position: 1; color: root.palmBottom }
                }
                PathSvg { path: root.spec.palm }
            }
            ShapePath {
                strokeColor: root.bodyEdge
                strokeWidth: 1.6
                joinStyle: ShapePath.RoundJoin
                fillColor: root.highlight === 0 ? root.lit : root.buttonFill
                Behavior on fillColor { ColorAnimation { duration: Theme.fast } }
                PathSvg { path: root.spec.left }
            }
            ShapePath {
                strokeColor: root.bodyEdge
                strokeWidth: 1.6
                joinStyle: ShapePath.RoundJoin
                fillColor: root.highlight === 1 ? root.lit : root.buttonFill
                Behavior on fillColor { ColorAnimation { duration: Theme.fast } }
                PathSvg { path: root.spec.right }
            }
            ShapePath {     // outline of the lit button on top, so its edge reads clearly
                strokeColor: root.highlight === 0 || root.highlight === 1 ? Theme.accentB : "transparent"
                strokeWidth: 1.8
                fillColor: "transparent"
                joinStyle: ShapePath.RoundJoin
                PathSvg { path: root.highlight === 1 ? root.spec.right : root.spec.left }
            }
        }

        Repeater {      // the two side buttons on the thumb side: index 0 = back (rear), 1 = forward (front)
            model: 2
            delegate: Rectangle {
                required property int index
                readonly property bool on: root.highlight === 3 + index
                x: root.spec.side[index].x
                y: root.spec.side[index].y
                width: root.spec.side[index].width
                height: root.spec.side[index].height
                radius: 3
                color: on ? root.lit : root.buttonFill
                border.width: on ? 1.8 : 1.4
                border.color: on ? Theme.accentB : root.bodyEdge
                Behavior on color { ColorAnimation { duration: Theme.fast } }
            }
        }

        Rectangle {     // scroll wheel
            x: root.spec.wheel.x
            y: root.spec.wheel.y
            width: root.spec.wheel.width
            height: root.spec.wheel.height
            radius: width / 2
            border.width: root.highlight === 2 ? 2 : 0
            border.color: Theme.accentB
            gradient: Gradient {
                GradientStop { position: 0; color: Theme.accentA }
                GradientStop { position: 1; color: Theme.accentB }
            }
            opacity: root.highlight === 2 ? 1 : 0.85
            Rectangle {     // a groove across the middle
                anchors.centerIn: parent
                width: parent.width - 4
                height: 1.5
                radius: 1
                color: Theme.dark ? Qt.rgba(0, 0, 0, 0.35) : Qt.rgba(1, 1, 1, 0.6)
            }
        }
    }
}
