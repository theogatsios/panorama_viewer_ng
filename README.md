# Panorama Viewer NG

A QGIS plugin for viewing 360° panoramic photos (photospheres) directly in QGIS, with a live direction sector on the map. Works on **QGIS 3.x (Qt5)** and **QGIS 4.x (Qt6)**. Built on [Pannellum](https://pannellum.org/) and [Three.js](https://threejs.org/).

Panorama Viewer NG is the next generation of [Panorama Viewer](https://github.com/pavelpereverzev/panorama_viewer) by Pavel Pereverzev, ported to QGIS 4 / Qt6 with new features.

## Features

- View 360° photos from local files or web URLs stored in a layer's attributes
- Draw a direction sector on the map that follows the view in real time
- Set the initial view angle from a layer field
- Choose between three viewers: Pannellum, Three.js, or Canvas, which needs no WebGL or GPU
- Expanded view and "open in browser" buttons on the viewer
- Browser window mode for a second screen: a browser window that follows your selection and turns the direction sector on the map

## Requirements

On QGIS 3.x with Qt5 the plugin uses QtWebKit and works out of the box.

On QGIS 4.x (Qt6) it uses QtWebEngine, which needs the Qt WebEngine Python bindings. These are included in some installers and are a separate package in others (for example `python3-pyqt6.qtwebengine` on Debian/Ubuntu). Without them the plugin still works, but panoramas open in your system web browser instead of inside QGIS.

## Quick guide

1. Open a layer of any geometry type.
2. Add a text field and fill it with direct links to 360° photos (local paths or web URLs).
3. Open the plugin and select the **Layer** and **Field** with the image links.
4. Select a feature on the map and click **View panorama**.

**Auto-update view** loads the panorama on every selection. **Show directions** draws the view sector on the map (the project CRS must be projected, not geographic). **Angles field** sets each panorama's initial heading. Hovering the bottom-right corner of the viewer shows buttons to open the panorama in your browser or to hide the controls and maximise the viewer.

## Browser window (second screen)

Tick **Browser window** in the plugin panel to show panoramas in your web browser instead of inside QGIS. A window opens once; drag it to your second screen and keep it there.

- With **Auto-update view** on, every feature you select replaces the panorama in that same window. No new tabs are opened.
- Looking around in the browser window turns the direction sector on the QGIS map, as with the built-in viewer.
- The badge in the top-left corner shows whether the window is linked to QGIS. If you close the window, the next selection opens it again.
- While the browser window is used, the panel shrinks to a compact bar with the link status, **Reopen window** and **Minimize panel**. Minimize hides the panel completely, and panoramas keep following your selection. Click the Panorama Viewer NG toolbar button to bring the panel back.
- Untick **Browser window** to bring panoramas back into the QGIS panel.

If QGIS has no embedded web engine, this mode is used automatically.

## Annotation

Save an image of an object seen in the panorama (a traffic sign, a pothole, a lamp post, a facade…) into a feature of any layer.

1. Open a panorama in the plugin panel, or in the browser window.
2. Select **one** feature on the map in the layer that should receive the image. Selecting it doesn't reload the panorama.
3. Click **Annotate**. The first time, choose:
   - the **target layer**;
   - the **image field**, a text field that receives the image as base64 JPEG;
   - the **object label** (e.g. `sign`, `pothole`), stored with every box;
   - whether to save the full view with the box drawn on it, or only the object (cropped, without the box).

   Click **…** next to Annotate to change these later. They're saved in the QGIS project, so each project keeps its own settings.
4. Drag a box around the object and press **Enter**. **Esc** cancels; **Backspace** removes the box.

**In the browser window** the same works there: click **Annotate** in the window's top-right corner (or the button in QGIS), draw the box, and press **Enter** or **Save to feature**. The window confirms when the image is saved, or tells you what's missing, e.g. no feature selected. Annotation in the browser works with the Pannellum and Canvas viewers. The saved image is rendered from the panorama itself, so it's sharp even when the viewer uses WebGL.

If the target layer also has fields named `bbox`, `annotations`, `source`, `yaw`, `pitch`, `fov` or `panorama`, those are filled too:
- `bbox`: the box in pixels;
- `annotations`: a JSON record with the label, image size, box and view angles (boxes in COCO-style `[x, y, w, h]`);
- `source`: the value `manual`;
- `yaw`, `pitch`, `fov`: the view angles;
- `panorama`: the panorama link.

## Troubleshooting

### "Embedded viewer not available"

On QGIS 4 (Qt6) the in-QGIS viewer needs the Qt WebEngine Python bindings. When they are missing, the plugin panel detects your system and shows the command to install them, with a **Copy** button and a **Check again** button. Panoramas open in your web browser until then.

| System | Command |
| --- | --- |
| Debian / Ubuntu / Mint | `sudo apt install python3-pyqt6.qtwebengine` |
| Fedora | `sudo dnf install python3-pyqt6-webengine` |
| Arch / Manjaro | `sudo pacman -S python-pyqt6-webengine` |
| openSUSE | find the name with `zypper search -s WebEngine \| grep -i pyqt` |
| Windows (OSGeo4W) | Run `osgeo4w-setup.exe` → Advanced Install → set **Root Directory** to the folder QGIS is installed in (e.g. `C:\\OSGeo4W`) → install `python3-pyqt6-webengine`. The "Local Package Directory" is only a download cache. |
| Windows (standalone installer) | Can't add packages; install QGIS via the OSGeo4W Network Installer, or use the browser window |
| Flatpak | not possible inside the Flatpak; install QGIS from your distribution or qgis.org |

Restart QGIS after installing.

Even with Qt WebEngine installed, some QGIS 4 builds (Windows OSGeo4W and Ubuntu included) start the embedded browser without GPU access, so WebGL is unavailable inside QGIS. The plugin then switches to the Canvas viewer automatically. For full quality, use the browser window.

Don't install `PyQt6-WebEngine` with **pip** into a system QGIS. The pip wheel bundles its own Qt, which won't match the Qt QGIS is built with and usually crashes QGIS.

### "Your browser does not have the necessary WebGL support"

Pannellum and Three.js need WebGL. Some QGIS builds start their embedded browser with the GPU switched off (`--disable-gpu`), so WebGL is unavailable whatever flags you set. You can confirm this by opening `chrome://gpu` in a QWebEngineView from the QGIS Python console.

The plugin detects this on the first panorama and switches to the **Canvas (no WebGL)** viewer, which draws the panorama on a plain 2D canvas. You can also pick it manually in the Viewer list. For the smoothest view, the message bar offers to open the panorama in your web browser.

Where QGIS allows it, the plugin also starts the embedded browser with `--ignore-gpu-blocklist --enable-unsafe-swiftshader`, which re-enables WebGL when the GPU driver is only blocklisted. Set `PANORAMA_VIEWER_NO_GPU_FLAGS=1` to turn this off.

## Building the plugin zip

```bash
git archive --prefix=panorama_viewer_ng/ -o panorama_viewer_ng.zip HEAD
```

Install it in QGIS via **Plugins → Manage and Install Plugins → Install from ZIP**.

## Credits and license

Original plugin © 2023 Pavel Pereverzev. Panorama Viewer NG © 2026 Theo Gatsios.

Licensed under the GNU General Public License v3.0 or later, see [LICENSE](LICENSE). Bundled libraries (Pannellum, three.js) are MIT-licensed, see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
