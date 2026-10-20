import QtQuick
import QtQuick.Shapes

// Inline vector icons on a 24x24 grid, stroked.
Item {
    id: root
    property string name: ""
    property color color: Theme.text
    property real size: 20
    property real strokeWidth: 1.8
    property bool filled: false

    // The layer gives smooth edges and makes the icon obey ancestor clipping (CurveRenderer ignores it).
    layer.enabled: true
    layer.samples: 4
    implicitWidth: size
    implicitHeight: size

    readonly property var paths: ({
        "shield": "M12 3 L19.5 6 V11.5 C19.5 16 16.5 19.5 12 21 C7.5 19.5 4.5 16 4.5 11.5 V6 Z",
        "pointer": "M5.5 3.5 L19 10.2 L12.6 12.4 L10.2 19 Z",
        "info": "M3 12 A9 9 0 1 1 21 12 A9 9 0 1 1 3 12 Z M12 11 V16.5 M12 7.7 V7.8",
        "minimize": "M6 12 H18",
        "close": "M7 7 L17 17 M17 7 L7 17",
        "plus": "M12 6 V18 M6 12 H18",
        "chevron": "M9 6 L15 12 L9 18",
        "mouse": "M12 3 C15.9 3 18 5.6 18 9 V15 C18 18.4 15.9 21 12 21 C8.1 21 6 18.4 6 15 V9 C6 5.6 8.1 3 12 3 Z M12 3 V9.5 M6 9.5 H18",
        "reset": "M4.5 12 A7.5 7.5 0 1 0 7 6.4 M4.5 4 V8.5 H9",
        "play": "M8 5.5 L18.5 12 L8 18.5 Z",
        "lock": "M6.5 11 H17.5 V19.5 H6.5 Z M8.5 11 V8 A3.5 3.5 0 0 1 15.5 8 V11",
        "alert": "M12 4 L21 19.5 H3 Z M12 10 V14 M12 16.6 V16.7",
        "check": "M5 12.5 L10 17.5 L19 7",
        "clock": "M3 12 A9 9 0 1 1 21 12 A9 9 0 1 1 3 12 Z M12 7 V12 L15.5 14",
        "power": "M12 3.5 V11.5 M7 6.5 A7.5 7.5 0 1 0 17 6.5",
        "wheel": "M12 3 V6 M12 18 V21 M8.5 8 H15.5 V16 H8.5 Z",
        "arrowUp": "M7 14 L12 9 L17 14",
        "arrowDown": "M7 10 L12 15 L17 10",
        "cursor": "M4 4 L10.5 20 L13 13 L20 10.5 Z",
        "gauge": "M4 17 A8.5 8.5 0 1 1 20 17 M12 13 L16 8.5",
        "sliders": "M5 7 H12 M16 7 H19 M5 17 H8 M12 17 H19 M14 4.5 V9.5 M10 14.5 V19.5",
        "block": "M3 12 A9 9 0 1 1 21 12 A9 9 0 1 1 3 12 Z M6 6 L18 18",
        "link": "M10 14 L14 10 M9 7.5 L10.8 5.7 A3.6 3.6 0 0 1 16 5.7 L18.3 8 A3.6 3.6 0 0 1 18.3 13.2 L16.5 15 M15 16.5 L13.2 18.3 A3.6 3.6 0 0 1 8 18.3 L5.7 16 A3.6 3.6 0 0 1 5.7 10.8 L7.5 9",
        "unlink": "M9 7.5 L10.8 5.7 A3.6 3.6 0 0 1 16 5.7 L18.3 8 A3.6 3.6 0 0 1 18.3 13.2 L17 14.5 M15 16.5 L13.2 18.3 A3.6 3.6 0 0 1 8 18.3 L5.7 16 A3.6 3.6 0 0 1 5.7 10.8 L7 9.5 M4 4 L6 6 M20 20 L18 18",
        "bolt": "M13.5 3 L6 13.5 H11.5 L10.5 21 L18 10.5 H12.5 Z",
        "wireless": "M4.5 9.5 A10 10 0 0 1 19.5 9.5 M7.5 13 A5.8 5.8 0 0 1 16.5 13 M12 17.2 V17.3",
        "plug": "M9 3 V8 M15 3 V8 M6.5 8 H17.5 V12 A5.5 5.5 0 0 1 6.5 12 Z M12 17.5 V21",
        "save": "M5 4 H16.5 L19.5 7 V20 H5 Z M8 4 V9 H15 V4 M8 20 V14 H16 V20",
        "external": "M14 4 H20 V10 M20 4 L11 13 M18 14 V19 H5 V6 H10",
        "hand": "M7 12 V6.5 A1.5 1.5 0 0 1 10 6.5 V11 M10 10.5 V5 A1.5 1.5 0 0 1 13 5 V11 M13 10.5 V6 A1.5 1.5 0 0 1 16 6 V13 C16 17 14 20 11 20 C8.5 20 7.5 18.5 6.5 16 L5 13.5 A1.4 1.4 0 0 1 7.3 12.2 Z"
    })

    Shape {
        width: 24
        height: 24
        scale: root.size / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.GeometryRenderer

        ShapePath {
            strokeColor: root.color
            strokeWidth: root.strokeWidth
            fillColor: root.filled ? root.color : "transparent"
            capStyle: ShapePath.RoundCap
            joinStyle: ShapePath.RoundJoin
            PathSvg { path: root.paths[root.name] || "" }
        }
    }
}
