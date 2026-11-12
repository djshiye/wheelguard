// Wheelguard panel icon and quick popup.
//
// The popup is built once from St widgets and only ever re-styled afterwards, so opening it is just
// showing a cached actor. State comes from the running app over the session bus
// (io.github.djshiye.Wheelguard, see wheelguard/gui/service.py): a StateChanged signal keeps the cache
// current and nothing here ever waits on a call.

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as Slider from 'resource:///org/gnome/shell/ui/slider.js';

const BUS_NAME = 'io.github.djshiye.Wheelguard';
const OBJECT_PATH = '/io/github/djshiye/Wheelguard';
const APP_ICON = 'io.github.djshiye.Wheelguard';
const START_COMMAND = ['wheelguard-settings', '--tray'];
const START_TIMEOUT_S = 10;
const FOCUS_DEBOUNCE_MS = 300;           // per-game profiles: a focus change must settle before the app is told
const STEAM_CLASS_RE = /^steam_app_\d+$/;   // a Steam game's window class
// A window with no launcher entry (a game from Lutris, Heroic or Wine) is known by its window class.
// The same shape the app accepts for an app id: letters, digits and . _ + - with spaces inside.
const WINDOW_CLASS_RE = /^[A-Za-z0-9_](?:[A-Za-z0-9._+ -]*[A-Za-z0-9._+-])?$/;
const MAX_APP_ID = 200;                  // longer ids are not sent (the app would refuse them anyway)

// Click-Lock hold-time slider: 1 to 15 s in half-second steps (wheelguard/gui/protocol.py, CLICKLOCK_MS_*).
const CLICKLOCK_MIN_MS = 1000;
const CLICKLOCK_MAX_MS = 15000;
const CLICKLOCK_STEP_MS = 500;
const CLICKLOCK_SEND_DELAY_MS = 300;     // keyboard and scroll changes are sent once they settle
const CLICKLOCK_MODES = [['repeat', 'Repeat clicks'], ['hold', 'Hold down']];

const IFACE_XML = `
<node>
  <interface name="io.github.djshiye.Wheelguard1">
    <method name="GetState"><arg type="a{sv}" direction="out" name="state"/></method>
    <method name="SetActiveStage"><arg type="i" direction="in" name="index"/></method>
    <method name="SetProtection"><arg type="b" direction="in" name="on"/></method>
    <method name="SetClickLock"><arg type="b" direction="in" name="on"/></method>
    <method name="SetClickLockMs"><arg type="i" direction="in" name="ms"/></method>
    <method name="SetClickLockMode"><arg type="s" direction="in" name="mode"/></method>
    <method name="FocusChanged"><arg type="s" direction="in" name="app_id"/></method>
    <method name="ShowWindow"/>
    <method name="Refresh"/>
    <signal name="StateChanged"><arg type="a{sv}" name="state"/></signal>
  </interface>
</node>`;
const WgProxy = Gio.DBusProxy.makeProxyWrapper(IFACE_XML);

// What the popup shows before the app has told us anything.
const EMPTY_STATE = {
    Online: false, Battery: -1, Charging: false, StageLabels: [], ActiveStage: -1, StageReason: '',
    ProtectionEnabled: true, DaemonState: 'connecting', Preset: '', Dropped: 0,
    StatusLabel: 'Connecting…', StatusTone: 'muted', ProtectionDetail: '', BatteryNote: 'Mouse not connected',
    ClickLockEnabled: false, ClickLockMs: 5000, ClickLockActive: false, ClickLockDetail: 'Off',
    ClickLockMode: 'repeat', BatteryLow: false, OfflineNote: 'Mouse asleep or not connected',
    ProfilesEnabled: false, ActiveProfile: '',
};

function msToFraction(ms) {
    return (Math.min(Math.max(ms, CLICKLOCK_MIN_MS), CLICKLOCK_MAX_MS) - CLICKLOCK_MIN_MS) /
        (CLICKLOCK_MAX_MS - CLICKLOCK_MIN_MS);
}

function fractionToMs(value) {
    const raw = CLICKLOCK_MIN_MS + value * (CLICKLOCK_MAX_MS - CLICKLOCK_MIN_MS);
    return Math.round(raw / CLICKLOCK_STEP_MS) * CLICKLOCK_STEP_MS;
}

function secondsText(ms) {
    return `${ms % 1000 ? (ms / 1000).toFixed(1) : ms / 1000} s`;
}

// Battery glyph colours (the same palette as stylesheet.css).
const PALETTE = {
    dark: {good: [0.239, 0.863, 0.592], amber: [1, 0.710, 0.278], coral: [1, 0.420, 0.478],
        faint: [0.400, 0.424, 0.486], bolt: [0.039, 0.043, 0.055]},
    light: {good: [0.086, 0.659, 0.439], amber: [0.890, 0.604, 0.106], coral: [0.910, 0.271, 0.353],
        faint: [0.541, 0.565, 0.627], bolt: [1, 1, 1]},
};

function unpack(dict) {
    const out = {};
    for (const [key, value] of Object.entries(dict))
        out[key] = value.recursiveUnpack();
    return out;
}

function makeLabel(text, styleClass) {
    return new St.Label({text, style_class: styleClass, y_align: Clutter.ActorAlign.CENTER});
}

function fileIcon(path) {
    return new Gio.FileIcon({file: Gio.File.new_for_path(path)});
}

const Indicator = GObject.registerClass(
class WheelguardIndicator extends PanelMenu.Button {
    _init(extension) {
        super._init(0.5, 'Wheelguard', false);
        this._extension = extension;
        this._state = {...EMPTY_STATE};
        this._running = false;
        this._starting = false;
        this._destroyed = false;
        this._light = false;
        this._chips = [];
        this._chipLabels = null;
        this._proxy = null;
        this._signalId = 0;
        this._watchId = 0;
        this._startTimeout = 0;
        this._clickLockTimeout = 0;
        this._focusId = 0;                  // per-game profiles: notify::focus-window handler
        this._focusTimeout = 0;
        this._lastFocusApp = null;          // the id the app was last told; null = tell it again
        this._clickLockSending = false;     // the slider shows the user's value until the app confirms it
        this._sliderDragging = false;
        this._settingSlider = false;
        this._cancellable = new Gio.Cancellable();

        this._icon = new St.Icon({
            gicon: fileIcon(`${extension.path}/icons/wheelguard-symbolic.svg`),
            style_class: 'system-status-icon wg-panel-icon',
        });
        // The icon with a small amber dot in its corner while the battery is low.
        const iconBox = new St.Widget({layout_manager: new Clutter.BinLayout(), y_align: Clutter.ActorAlign.CENTER});
        iconBox.add_child(this._icon);
        this._lowDot = new St.Widget({style_class: 'wg-low-dot', visible: false, x_expand: true, y_expand: true,
            x_align: Clutter.ActorAlign.END, y_align: Clutter.ActorAlign.START});
        iconBox.add_child(this._lowDot);
        // On hover (menu closed) the battery percentage shows next to the icon: shell panel buttons have no tooltips.
        this._hoverLabel = new St.Label({style_class: 'wg-hover-label', visible: false,
            y_align: Clutter.ActorAlign.CENTER});
        const panelRow = new St.BoxLayout({y_align: Clutter.ActorAlign.CENTER});
        panelRow.add_child(iconBox);
        panelRow.add_child(this._hoverLabel);
        this.add_child(panelRow);
        this.connect('notify::hover', () => this._updateHover());

        this.menu.actor.add_style_class_name('wg-boxpointer');
        this.menu.box.add_style_class_name('wg-menu');
        this._buildPopup();

        this._settings = St.Settings.get();
        this._schemeId = this._settings.connect('notify::color-scheme', () => this._applyScheme());
        this._applyScheme();

        this.menu.connect('open-state-changed', (_menu, open) => {
            this._updateHover();
            if (open && this._proxy)
                this._proxy.RefreshRemote(() => {});          // fire and forget: the signal brings the result
        });

        this._render();
        this.connect('destroy', () => this._cleanup());
        this._focusId = global.display.connect('notify::focus-window', () => this._focusSoon());
        this._watchId = Gio.bus_watch_name(Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this._appeared(), () => this._vanished());
    }

    // ------------------------------------------------------------------ popup widgets
    _buildPopup() {
        this._root = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'wg-root'});
        this.menu.box.add_child(this._root);

        // header: logo + name open the app, the pill shows the status, the button opens the app too
        const header = new St.BoxLayout({style_class: 'wg-header'});
        const home = new St.Button({style_class: 'wg-home', can_focus: true, x_expand: false});
        const homeRow = new St.BoxLayout({style_class: 'wg-home-row'});
        homeRow.add_child(new St.Icon({
            gicon: new Gio.ThemedIcon({name: APP_ICON}), icon_size: 28, style_class: 'wg-logo',
            y_align: Clutter.ActorAlign.CENTER,
        }));
        const title = makeLabel('Wheelguard', 'wg-title');
        title.clutter_text.ellipsize = 0;       // Pango.EllipsizeMode.NONE: the name keeps its natural width
        homeRow.add_child(title);
        home.set_child(homeRow);
        home.connect('clicked', () => this._showWindow());
        header.add_child(home);
        header.add_child(new St.Widget({x_expand: true}));

        this._pill = new St.BoxLayout({style_class: 'wg-pill', y_align: Clutter.ActorAlign.CENTER});
        const glow = new St.Bin({style_class: 'wg-glow', y_align: Clutter.ActorAlign.CENTER});
        glow.set_child(new St.Widget({style_class: 'wg-dot', x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER}));
        this._pill.add_child(glow);
        this._pillLabel = makeLabel('', 'wg-pill-label');
        this._pillLabel.clutter_text.ellipsize = 0;
        this._pill.add_child(this._pillLabel);
        header.add_child(this._pill);

        const open = new St.Button({style_class: 'wg-icon-btn', can_focus: true,
            y_align: Clutter.ActorAlign.CENTER});
        open.set_child(new St.Icon({
            gicon: fileIcon(`${this._extension.path}/icons/external-symbolic.svg`),
            icon_size: 16, style_class: 'wg-icon-btn-icon',
        }));
        open.connect('clicked', () => this._showWindow());
        header.add_child(open);
        this._root.add_child(header);

        this._body = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'wg-body'});
        this._root.add_child(this._body);
        this._buildProfile();
        this._buildAsleep();
        this._buildBattery();
        this._buildProtection();
        this._buildClickLock();
        this._buildDpi();

        this._offline = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'wg-card wg-offline'});
        this._offlineText = makeLabel("Wheelguard isn't running", 'wg-card-title');
        this._offlineHint = makeLabel('Start it to see your mouse and protection here.', 'wg-card-sub');
        this._startButton = new St.Button({style_class: 'wg-start', can_focus: true, x_expand: true});
        this._startLabel = new St.Label({text: 'Start Wheelguard', style_class: 'wg-start-label',
            x_align: Clutter.ActorAlign.CENTER});
        this._startButton.set_child(this._startLabel);
        this._startButton.connect('clicked', () => this._start());
        this._offline.add_child(this._offlineText);
        this._offline.add_child(this._offlineHint);
        this._offline.add_child(this._startButton);
        this._root.add_child(this._offline);
    }

    _card() {
        return new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, style_class: 'wg-card'});
    }

    // "Profile: <app>" while a per-game profile is engaged.
    _buildProfile() {
        const card = this._card();
        this._profileLabel = makeLabel('', 'wg-card-sub');
        this._profileLabel.clutter_text.ellipsize = 3;      // Pango.EllipsizeMode.END
        card.add_child(this._profileLabel);
        this._profileCard = card;
        this._body.add_child(card);
    }

    // One calm row instead of an empty battery card and an empty DPI card.
    _buildAsleep() {
        const card = this._card();
        const row = new St.BoxLayout({style_class: 'wg-row'});
        this._asleepGlyph = new St.DrawingArea({style_class: 'wg-battery', width: 42, height: 20,
            y_align: Clutter.ActorAlign.CENTER, opacity: 120});
        this._asleepGlyph.connect('repaint', area => this._paintBattery(area, true));
        row.add_child(this._asleepGlyph);
        this._asleepText = makeLabel(EMPTY_STATE.OfflineNote, 'wg-card-title');
        // Wrap instead of cutting the note off: it is too long for one line beside the glyph.
        this._asleepText.x_expand = true;
        this._asleepText.clutter_text.line_wrap = true;
        this._asleepText.clutter_text.ellipsize = 0;   // Pango.EllipsizeMode.NONE
        row.add_child(this._asleepText);
        card.add_child(row);
        this._asleepCard = card;
        this._body.add_child(card);
    }

    _buildBattery() {
        const card = this._card();
        this._batteryCard = card;
        const row = new St.BoxLayout({style_class: 'wg-row'});
        this._battery = new St.DrawingArea({style_class: 'wg-battery', width: 42, height: 20,
            y_align: Clutter.ActorAlign.CENTER});
        this._battery.connect('repaint', area => this._paintBattery(area));
        row.add_child(this._battery);
        this._percent = makeLabel('—', 'wg-percent');
        row.add_child(this._percent);
        row.add_child(new St.Widget({x_expand: true}));
        this._batteryNote = makeLabel('', 'wg-note');
        row.add_child(this._batteryNote);
        card.add_child(row);
        this._body.add_child(card);
    }

    _buildProtection() {
        const card = this._card();
        const row = new St.BoxLayout({style_class: 'wg-row'});
        const text = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true, y_align: Clutter.ActorAlign.CENTER});
        text.add_child(new St.Label({text: 'Scroll protection', style_class: 'wg-card-title'}));
        this._protectDetail = new St.Label({style_class: 'wg-card-sub'});
        this._protectDetail.clutter_text.ellipsize = 3;     // Pango.EllipsizeMode.END
        text.add_child(this._protectDetail);
        row.add_child(text);

        this._switch = this._makeSwitch(() => this._toggleProtection());
        row.add_child(this._switch);
        card.add_child(row);
        this._body.add_child(card);
    }

    _makeSwitch(onClicked) {
        const button = new St.Button({style_class: 'wg-switch', can_focus: true,
            y_align: Clutter.ActorAlign.CENTER});
        const track = new St.Widget({layout_manager: new Clutter.FixedLayout(), x_expand: true, y_expand: true});
        const thumb = new St.Widget({style_class: 'wg-thumb'});
        thumb.set_size(20, 20);
        thumb.set_position(4, 4);
        track.add_child(thumb);
        button.set_child(track);
        button._wgThumb = thumb;
        button.connect('clicked', onClicked);
        return button;
    }

    // Click-Lock: hold the main button and Wheelguard keeps clicking until the next click.
    _buildClickLock() {
        const card = this._card();
        const row = new St.BoxLayout({style_class: 'wg-row'});
        const text = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, x_expand: true,
            y_align: Clutter.ActorAlign.CENTER});
        const titleRow = new St.BoxLayout({style_class: 'wg-title-row'});
        titleRow.add_child(new St.Label({text: 'Click-Lock', style_class: 'wg-card-title',
            y_align: Clutter.ActorAlign.CENTER}));
        this._clickLockLive = new St.BoxLayout({style_class: 'wg-live', y_align: Clutter.ActorAlign.CENTER});
        this._clickLockLive.add_child(new St.Widget({style_class: 'wg-live-dot', y_align: Clutter.ActorAlign.CENTER}));
        this._clickLockLiveLabel = makeLabel('Clicking', 'wg-live-label');
        this._clickLockLive.add_child(this._clickLockLiveLabel);
        titleRow.add_child(this._clickLockLive);
        text.add_child(titleRow);
        this._clickLockDetail = new St.Label({style_class: 'wg-card-sub'});
        this._clickLockDetail.clutter_text.ellipsize = 3;    // Pango.EllipsizeMode.END
        text.add_child(this._clickLockDetail);
        row.add_child(text);
        this._clickLockSwitch = this._makeSwitch(() => this._toggleClickLock());
        row.add_child(this._clickLockSwitch);
        card.add_child(row);

        this._clickLockSeg = new St.BoxLayout({style_class: 'wg-seg wg-seg-small', reactive: true, x_expand: true});
        this._clickLockSeg.layout_manager.homogeneous = true;
        this._clickLockModeChips = CLICKLOCK_MODES.map(([mode, text]) => {
            const chip = new St.Button({style_class: 'wg-chip', can_focus: true, x_expand: true, label: text});
            chip.connect('clicked', () => this._setClickLockMode(mode));
            this._clickLockSeg.add_child(chip);
            return chip;
        });
        card.add_child(this._clickLockSeg);

        const sliderHead = new St.BoxLayout({style_class: 'wg-row wg-slider-head'});
        sliderHead.add_child(new St.Label({text: 'Hold for', style_class: 'wg-card-sub', x_expand: true,
            y_align: Clutter.ActorAlign.CENTER}));
        this._clickLockValue = makeLabel('', 'wg-dpi-active');
        sliderHead.add_child(this._clickLockValue);
        card.add_child(sliderHead);

        this._clickLockSlider = new Slider.Slider(msToFraction(EMPTY_STATE.ClickLockMs));
        this._clickLockSlider.add_style_class_name('wg-slider');
        this._clickLockSlider.accessible_name = 'Click-Lock hold time';
        this._clickLockSlider.connect('notify::value', () => this._onClickLockSlider());
        this._clickLockSlider.connect('drag-begin', () => {
            this._sliderDragging = true;
        });
        this._clickLockSlider.connect('drag-end', () => {
            this._sliderDragging = false;
            this._sendClickLockMs(0);
        });
        card.add_child(this._clickLockSlider);
        this._clickLockSliderBox = [this._clickLockSeg, sliderHead, this._clickLockSlider];
        this._body.add_child(card);
    }

    _buildDpi() {
        const card = this._card();
        const head = new St.BoxLayout({style_class: 'wg-row'});
        const title = new St.Label({text: 'DPI', style_class: 'wg-card-title', x_expand: true,
            y_align: Clutter.ActorAlign.CENTER});
        head.add_child(title);
        this._dpiActive = makeLabel('', 'wg-dpi-active');
        head.add_child(this._dpiActive);
        card.add_child(head);

        this._seg = new St.BoxLayout({style_class: 'wg-seg', reactive: true, x_expand: true});
        this._seg.layout_manager.homogeneous = true;           // equal-width chips
        this._seg.connect('scroll-event', (_actor, event) => this._onScroll(event));
        card.add_child(this._seg);

        this._reason = new St.Label({style_class: 'wg-card-sub wg-reason'});
        this._reason.clutter_text.line_wrap = true;
        card.add_child(this._reason);
        this._dpiCard = card;
        this._body.add_child(card);
    }

    // ------------------------------------------------------------------ painting
    _paintBattery(area, dimmed = false) {
        const palette = PALETTE[this._light ? 'light' : 'dark'];
        const s = dimmed ? EMPTY_STATE : this._state;
        const known = s.Online && s.Battery >= 0;
        const tone = !known ? palette.faint
            : (s.Battery <= 15 ? palette.coral : (s.Battery <= 30 ? palette.amber : palette.good));
        const cr = area.get_context();
        const bodyW = 36.5, bodyH = 19, r = 5.4, line = 2;

        const roundRect = (x, y, w, h, rad) => {
            cr.newSubPath();
            cr.arc(x + w - rad, y + rad, rad, -Math.PI / 2, 0);
            cr.arc(x + w - rad, y + h - rad, rad, 0, Math.PI / 2);
            cr.arc(x + rad, y + h - rad, rad, Math.PI / 2, Math.PI);
            cr.arc(x + rad, y + rad, rad, Math.PI, 1.5 * Math.PI);
            cr.closePath();
        };

        cr.setSourceRGB(...tone);
        cr.setLineWidth(line);
        roundRect(line / 2, line / 2 + 0.5, bodyW - line, bodyH - line, r - 1);
        cr.stroke();
        if (known && s.Battery > 0) {
            const inner = bodyW - 8;
            const w = Math.max(4, inner * s.Battery / 100);
            roundRect(4, 4.5, w, bodyH - 8, 2.7);
            cr.fill();
        }
        roundRect(bodyW + 1, 0.5 + (bodyH - 8) / 2, 3.4, 8, 1.3);         // nub
        cr.fill();
        if (s.Online && s.Charging) {
            cr.setSourceRGB(...palette.bolt);
            const cx = bodyW / 2, cy = 0.5 + bodyH / 2;
            cr.moveTo(cx + 1.4, cy - 7);
            cr.lineTo(cx - 4.4, cy + 0.9);
            cr.lineTo(cx - 0.4, cy + 0.9);
            cr.lineTo(cx - 1.6, cy + 7);
            cr.lineTo(cx + 4.4, cy - 1.1);
            cr.lineTo(cx + 0.4, cy - 1.1);
            cr.closePath();
            cr.fill();
        }
        cr.$dispose();
    }

    _applyScheme() {
        const light = this._settings.color_scheme === St.SystemColorScheme.PREFER_LIGHT;
        this._light = light;
        if (light)
            this.menu.box.add_style_class_name('wg-light');
        else
            this.menu.box.remove_style_class_name('wg-light');
        this._battery.queue_repaint();
        this._asleepGlyph.queue_repaint();
    }

    // Percentage beside the panel icon while hovering it (and the same words for screen readers).
    _updateHover() {
        const s = this._state;
        const known = this._running && s.DaemonState === 'running' && s.Online && s.Battery >= 0;
        this._hoverLabel.text = known ? `${s.Battery}%` : '';
        this._hoverLabel.visible = known && this.hover && !this.menu.isOpen;
        this.accessible_name = known ? `Wheelguard, mouse battery ${s.Battery}%` : 'Wheelguard';
    }

    // ------------------------------------------------------------------ rendering the cached state
    _render() {
        const s = this._state;
        const running = this._running;
        this._icon.opacity = running ? 255 : 110;
        this._lowDot.visible = running && s.BatteryLow;
        this._updateHover();
        this._body.visible = running;
        this._offline.visible = !running;

        for (const tone of ['good', 'amber', 'coral', 'muted'])
            this._pill.remove_style_class_name(`wg-tone-${tone}`);
        const tone = running ? s.StatusTone : 'muted';
        this._pill.add_style_class_name(`wg-tone-${tone}`);
        this._pillLabel.text = running ? s.StatusLabel : 'Not running';

        if (!running) {
            if (!this._starting) {
                this._startLabel.text = 'Start Wheelguard';
                this._startButton.reactive = true;
                this._startButton.remove_style_class_name('wg-busy');
            }
            return;
        }

        const mouseHere = s.Online && s.DaemonState === 'running';     // never show a stale battery
        this._batteryCard.visible = mouseHere;
        this._dpiCard.visible = mouseHere && s.StageLabels.length > 0;    // a mouse known only by its battery has none
        this._asleepCard.visible = !mouseHere;
        this._asleepText.text = s.OfflineNote || EMPTY_STATE.OfflineNote;
        const known = mouseHere && s.Battery >= 0;
        this._percent.text = known ? `${s.Battery}%` : '—';
        if (known)
            this._percent.remove_style_class_name('wg-faint');
        else
            this._percent.add_style_class_name('wg-faint');
        this._batteryNote.text = s.BatteryNote;
        if (s.Online && s.Charging)
            this._batteryNote.add_style_class_name('wg-good');
        else
            this._batteryNote.remove_style_class_name('wg-good');
        this._battery.queue_repaint();

        this._profileCard.visible = s.ProfilesEnabled && s.ActiveProfile !== '';
        this._profileLabel.text = `Profile: ${s.ActiveProfile}`;
        this._protectDetail.text = s.ProtectionDetail;
        const canSwitch = ['running', 'waiting', 'paused'].includes(s.DaemonState);    // paused: switching on resumes
        this._switch.reactive = canSwitch;
        this._switch.opacity = canSwitch ? 255 : 128;
        this._setSwitch(this._switch, s.ProtectionEnabled);

        this._renderClickLock(canSwitch);
        this._renderChips();
    }

    _setSwitch(button, on) {
        if (on)
            button.add_style_class_name('wg-on');
        else
            button.remove_style_class_name('wg-on');
        const thumb = button._wgThumb;
        const x = on ? 26 : 4;
        if (thumb.x !== x) {
            thumb.remove_all_transitions();
            thumb.ease({x, duration: 200, mode: Clutter.AnimationMode.EASE_OUT_CUBIC});
        }
    }

    _renderClickLock(canSwitch) {
        const s = this._state;
        this._clickLockSwitch.reactive = canSwitch;
        this._clickLockSwitch.opacity = canSwitch ? 255 : 128;
        this._setSwitch(this._clickLockSwitch, s.ClickLockEnabled);
        this._clickLockDetail.text = s.ClickLockDetail;
        this._clickLockLive.visible = s.ClickLockActive;
        this._clickLockLiveLabel.text = s.ClickLockMode === 'hold' ? 'Holding' : 'Clicking';
        const usable = canSwitch && s.ClickLockEnabled;
        this._clickLockSlider.reactive = usable;
        this._clickLockSlider.can_focus = usable;
        for (const actor of this._clickLockSliderBox) {
            actor.visible = s.ClickLockEnabled;     // its settings only show while Click-Lock is on
            actor.opacity = usable ? 255 : 110;
        }
        CLICKLOCK_MODES.forEach(([mode], i) => {
            const chip = this._clickLockModeChips[i];
            chip.reactive = usable;
            if (mode === s.ClickLockMode)
                chip.add_style_class_name('wg-chip-active');
            else
                chip.remove_style_class_name('wg-chip-active');
        });
        if (!this._sliderDragging && !this._clickLockSending) {    // never yank the slider from under the user
            this._settingSlider = true;
            this._clickLockSlider.value = msToFraction(s.ClickLockMs);
            this._settingSlider = false;
            this._clickLockValue.text = secondsText(s.ClickLockMs);
        }
    }

    _renderChips() {
        const s = this._state;
        const labels = s.StageLabels;
        const key = labels.join('|');
        if (key !== this._chipLabels) {            // the stage list changed: rebuild the chips
            this._chipLabels = key;
            this._seg.destroy_all_children();
            this._chips = labels.map((text, index) => {
                const chip = new St.Button({style_class: 'wg-chip', can_focus: true, x_expand: true,
                    label: text});
                chip.connect('clicked', () => this._setStage(index));
                this._seg.add_child(chip);
                return chip;
            });
        }
        const enabled = s.StageReason === '' && labels.length > 0;
        this._seg.visible = labels.length > 0;
        this._seg.opacity = enabled ? 255 : 128;
        this._chips.forEach((chip, i) => {
            chip.reactive = enabled;
            if (i === s.ActiveStage)
                chip.add_style_class_name('wg-chip-active');
            else
                chip.remove_style_class_name('wg-chip-active');
        });
        const active = s.ActiveStage >= 0 && s.ActiveStage < labels.length;
        this._dpiActive.text = active ? `${labels[s.ActiveStage]} DPI` : '';
        this._reason.text = s.StageReason;
        this._reason.visible = s.StageReason !== '';
    }

    // ------------------------------------------------------------------ actions
    _setStage(index) {
        if (!this._proxy || index === this._state.ActiveStage)
            return;
        this._state = {...this._state, ActiveStage: index};     // optimistic; _pull() corrects a refusal
        this._render();
        this._proxy.SetActiveStageRemote(index, () => this._pull());
    }

    _onScroll(event) {
        const s = this._state;
        if (!this._proxy || s.StageReason !== '' || s.StageLabels.length === 0)
            return Clutter.EVENT_PROPAGATE;
        const dir = event.get_scroll_direction();
        let step = 0;
        if (dir === Clutter.ScrollDirection.UP)
            step = 1;
        else if (dir === Clutter.ScrollDirection.DOWN)
            step = -1;
        else if (dir === Clutter.ScrollDirection.SMOOTH)
            step = event.get_scroll_delta()[1] < 0 ? 1 : -1;
        const next = s.ActiveStage + step;
        if (step !== 0 && next >= 0 && next < s.StageLabels.length)
            this._setStage(next);
        return Clutter.EVENT_STOP;
    }

    _toggleProtection() {
        if (!this._proxy)
            return;
        const on = !this._state.ProtectionEnabled;
        this._state = {...this._state, ProtectionEnabled: on};
        this._render();
        this._proxy.SetProtectionRemote(on, () => this._pull());
    }

    _toggleClickLock() {
        if (!this._proxy)
            return;
        const on = !this._state.ClickLockEnabled;
        this._state = {...this._state, ClickLockEnabled: on,
            ClickLockDetail: on ? `Hold the main button ${secondsText(this._state.ClickLockMs)} to start` : 'Off'};
        this._render();
        this._proxy.SetClickLockRemote(on, () => this._pull());
    }

    _setClickLockMode(mode) {
        if (!this._proxy || mode === this._state.ClickLockMode)
            return;
        this._state = {...this._state, ClickLockMode: mode};      // optimistic; _pull() corrects a refusal
        this._render();
        this._proxy.SetClickLockModeRemote(mode, () => this._pull());
    }

    _onClickLockSlider() {
        if (this._settingSlider)
            return;
        const ms = fractionToMs(this._clickLockSlider.value);
        this._clickLockValue.text = secondsText(ms);
        this._clickLockSending = true;
        if (!this._sliderDragging)
            this._sendClickLockMs(CLICKLOCK_SEND_DELAY_MS);
    }

    _sendClickLockMs(delay) {
        this._clearClickLockTimeout();
        this._clickLockTimeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
            this._clickLockTimeout = 0;
            const ms = fractionToMs(this._clickLockSlider.value);
            if (!this._proxy || ms === this._state.ClickLockMs) {
                this._clickLockSending = false;
                this._render();
                return GLib.SOURCE_REMOVE;
            }
            this._state = {...this._state, ClickLockMs: ms};
            this._proxy.SetClickLockMsRemote(ms, () => {
                this._clickLockSending = false;
                this._pull();
            });
            return GLib.SOURCE_REMOVE;
        });
    }

    _clearClickLockTimeout() {
        if (this._clickLockTimeout) {
            GLib.source_remove(this._clickLockTimeout);
            this._clickLockTimeout = 0;
        }
    }

    _showWindow() {
        this.menu.close();
        if (this._proxy)
            this._proxy.ShowWindowRemote(() => {});
    }

    _start() {
        if (this._starting)
            return;
        try {
            Gio.Subprocess.new(START_COMMAND, Gio.SubprocessFlags.NONE);
        } catch (e) {
            this._offlineHint.text = `Could not start: ${e.message}`;
            return;
        }
        this._starting = true;
        this._startLabel.text = 'Starting…';
        this._startButton.reactive = false;
        this._startButton.add_style_class_name('wg-busy');
        this._startTimeout = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, START_TIMEOUT_S, () => {
            this._startTimeout = 0;
            this._starting = false;
            this._render();
            return GLib.SOURCE_REMOVE;
        });
    }

    // ------------------------------------------------------------------ the app on the bus
    _appeared() {
        if (this._proxy)
            return;
        new WgProxy(Gio.DBus.session, BUS_NAME, OBJECT_PATH, (proxy, error) => {
            if (error) {
                if (!error.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                    console.error(`Wheelguard: ${error.message}`);
                return;
            }
            if (this._proxy || this._cancellable.is_cancelled())
                return;
            if (proxy.g_name_owner === null)    // the app quit while the proxy was being made
                return;
            this._proxy = proxy;
            this._signalId = proxy.connectSignal('StateChanged',
                (_proxy, _sender, [state]) => this._setState(unpack(state)));
            this._running = true;
            this._starting = false;
            this._clearStartTimeout();
            this._pull();
        }, this._cancellable, Gio.DBusProxyFlags.DO_NOT_AUTO_START);
    }

    _vanished() {
        if (this._destroyed)            // unwatching a name that is up reports it as gone
            return;
        this._dropProxy();
        this._running = false;
        this._state = {...EMPTY_STATE};
        this._render();
    }

    _dropProxy() {
        if (this._proxy && this._signalId)
            this._proxy.disconnectSignal(this._signalId);
        this._signalId = 0;
        this._proxy = null;
        this._lastFocusApp = null;
        this._clearFocusTimeout();
    }

    _pull() {
        if (!this._proxy)
            return;
        this._proxy.GetStateRemote((result, error) => {
            if (!error && this._proxy)
                this._setState(unpack(result[0]));
        });
    }

    _setState(state) {
        const was = this._state.ProfilesEnabled;
        this._state = {...EMPTY_STATE, ...state};
        this._render();
        if (this._state.ProfilesEnabled !== was)
            this._lastFocusApp = null;      // just switched on (or off): the app needs the current answer
        if (this._state.ProfilesEnabled && this._lastFocusApp === null)
            this._focusSoon();
    }

    // ------------------------------------------------------------------ per-game profiles
    // Only the focused app's id goes to the app, and only while "Per-game profiles" is on: no titles, no
    // other windows. Changes are debounced and an unchanged id is not sent again.
    _focusSoon() {
        if (this._destroyed || !this._proxy || !this._state.ProfilesEnabled)
            return;
        if (this._focusTimeout)
            GLib.source_remove(this._focusTimeout);
        this._focusTimeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, FOCUS_DEBOUNCE_MS, () => {
            this._focusTimeout = 0;
            this._sendFocus();
            return GLib.SOURCE_REMOVE;
        });
    }

    // The focused app's desktop-file id. Games rarely match a desktop file, so the tracker gives a
    // window-backed app ("window:NN"); then the window's WM class is sent instead (a Steam game's
    // "steam_app_<n>", or the program's own class for Lutris, Heroic and Wine). Never a window title.
    _focusedAppId() {
        const win = global.display.focus_window;
        if (!win)
            return '';
        const wmClass = win.get_wm_class?.() ?? '';
        if (STEAM_CLASS_RE.test(wmClass))
            return wmClass;
        const app = Shell.WindowTracker.get_default().get_window_app(win);
        const id = app?.get_id() ?? '';
        if (!app || app.is_window_backed?.() || id.startsWith('window:'))
            return wmClass.length <= MAX_APP_ID && WINDOW_CLASS_RE.test(wmClass) ? wmClass : '';
        return id.length <= MAX_APP_ID ? id : '';
    }

    _sendFocus() {
        if (!this._proxy || !this._state.ProfilesEnabled)
            return;
        const id = this._focusedAppId();
        if (id === this._lastFocusApp)
            return;
        this._lastFocusApp = id;
        this._proxy.FocusChangedRemote(id, () => {});
    }

    _clearFocusTimeout() {
        if (this._focusTimeout) {
            GLib.source_remove(this._focusTimeout);
            this._focusTimeout = 0;
        }
    }

    _clearStartTimeout() {
        if (this._startTimeout) {
            GLib.source_remove(this._startTimeout);
            this._startTimeout = 0;
        }
    }

    // Runs from the actor's 'destroy' signal, so also when the shell tears the panel down itself.
    _cleanup() {
        this._destroyed = true;
        this._cancellable.cancel();
        this._clearStartTimeout();
        this._clearClickLockTimeout();
        this._clearFocusTimeout();
        if (this._focusId)
            global.display.disconnect(this._focusId);
        this._focusId = 0;
        if (this._watchId)
            Gio.bus_unwatch_name(this._watchId);
        this._watchId = 0;
        this._dropProxy();
        if (this._schemeId)
            this._settings.disconnect(this._schemeId);
        this._schemeId = 0;
    }
});

export default class WheelguardExtension extends Extension {
    enable() {
        this._indicator = new Indicator(this);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
    }

    disable() {
        this._indicator?.destroy();
        this._indicator = null;
    }
}
