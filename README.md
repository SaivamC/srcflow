# srcflow

**Dependency flow visualizer for any JS / TS / JSX / TSX repository.**

Generates a single interactive HTML file — no server, no config, no dependencies.
Every file is a box. Every `import` is an arrow. Click any file to see exactly what it depends on and what depends on it.

![srcflow screenshot](https://raw.githubusercontent.com/SaivamC/srcflow/main/screenshot.png)

## Install

```bash
pip install srcflow
```

Requires Python 3.9+. No other dependencies.

## Usage

```bash
# inside any JS/TS repo — generates depflow.html
cd /your/repo
srcflow

# target a specific repo from anywhere
srcflow /path/to/repo

# custom output path
srcflow /path/to/repo --out ~/Desktop/myrepo.html

# override source root (if not src/ or lib/)
srcflow /path/to/repo --src /path/to/repo/packages/web/src
```

## What you get

Open `depflow.html` in any browser:

- **5 columns** auto-detected from your directory names: Model → Pages → Components → Services/Effects → Utils
- **186 real import edges** drawn as curved arrows between files
- **Click any file** → its imports highlight blue (↓), files that import it highlight red (↑)
- **Double-click** → smooth animated zoom into that file
- **Search** → type any filename, jump with ↑↓, press Enter to focus
- **Column tabs** → click a layer name to zoom the entire column into view
- **Scroll to zoom** at cursor · drag to pan · pinch on trackpad

## Supported languages

`.js` `.jsx` `.ts` `.tsx` `.mjs` `.cjs`

Parses ES6 `import` statements and CommonJS `require()` calls.

## License

MIT — Saivamsi Chakrala
