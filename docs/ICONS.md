# Icon Assets State

## Current Identifier

- Desktop entry file: `packaging/deb/org.docking.Docking.desktop`
- Desktop entry icon name: `Icon=org.docking.Docking`

## Source Files Used

- App icon source: `images/docking.png` (RGBA)
- Symbolic source currently copied into packaging: `symbolic.svg`
- Additional trace outputs created during this work:
  - `docking.svg`
  - `org.docking.Docking-symbolic.svg`
  - `docking.bmp`
  - `docking.pbm`

## Commands Run

PNG to SVG tracing commands that were executed:

```bash
convert images/docking.png docking.bmp
potrace -s docking.bmp -o docking.svg

convert images/docking.png -resize 512x512 -colorspace Gray -threshold 60% \
  -morphology Open Square:1 -morphology Close Square:1 docking.pbm
potrace -s docking.pbm -o org.docking.Docking-symbolic.svg
```

Commands used to generate the currently packaged app icons:

```bash
convert images/docking.png -trim +repage -gravity center -background none -extent 1024x1024 /tmp/docking_master_prepped.png

for s in 16 24 32 48 64 128 256 512; do
  mkdir -p "packaging/deb/icons/hicolor/${s}x${s}/apps"
  convert /tmp/docking_master_prepped.png -filter Lanczos -resize "${s}x${s}" \
    "packaging/deb/icons/hicolor/${s}x${s}/apps/org.docking.Docking.png"
done
```

Commands used to generate/copy the currently packaged symbolic and status icons:

```bash
mkdir -p packaging/deb/icons/hicolor/symbolic/apps
mkdir -p packaging/deb/icons/hicolor/symbolic/status
cp symbolic.svg packaging/deb/icons/hicolor/symbolic/apps/org.docking.Docking-symbolic.svg
cp symbolic.svg packaging/deb/icons/hicolor/symbolic/status/org.docking.Docking-symbolic.svg

for s in 16 22 24; do
  mkdir -p "packaging/deb/icons/hicolor/${s}x${s}/status"
  rsvg-convert -w "$s" -h "$s" symbolic.svg \
    -o "packaging/deb/icons/hicolor/${s}x${s}/status/org.docking.Docking.png"
done
```

## Current Packaged Icon Files

`packaging/deb/icons/hicolor/128x128/apps/org.docking.Docking.png`
`packaging/deb/icons/hicolor/16x16/apps/org.docking.Docking.png`
`packaging/deb/icons/hicolor/16x16/status/org.docking.Docking.png`
`packaging/deb/icons/hicolor/22x22/status/org.docking.Docking.png`
`packaging/deb/icons/hicolor/24x24/apps/org.docking.Docking.png`
`packaging/deb/icons/hicolor/24x24/status/org.docking.Docking.png`
`packaging/deb/icons/hicolor/256x256/apps/org.docking.Docking.png`
`packaging/deb/icons/hicolor/32x32/apps/org.docking.Docking.png`
`packaging/deb/icons/hicolor/48x48/apps/org.docking.Docking.png`
`packaging/deb/icons/hicolor/512x512/apps/org.docking.Docking.png`
`packaging/deb/icons/hicolor/64x64/apps/org.docking.Docking.png`
`packaging/deb/icons/hicolor/symbolic/apps/org.docking.Docking-symbolic.svg`
`packaging/deb/icons/hicolor/symbolic/status/org.docking.Docking-symbolic.svg`

## Deb Packaging Behavior

`packaging/deb/debian/rules` copies `packaging/deb/icons/hicolor` into
`/usr/share/icons/hicolor` during deb build.
