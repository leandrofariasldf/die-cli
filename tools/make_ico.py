from PIL import Image
from pathlib import Path

SRC = Path("assets/icon.png")
OUT = Path("assets/icon.ico")

# Tamanhos clássicos que o Windows usa no Explorer/atalhos
SIZES = [(16,16), (24,24), (32,32), (48,48), (64,64), (128,128), (256,256)]

if not SRC.exists():
    raise SystemExit(f"Arquivo não encontrado: {SRC} (coloque um PNG em assets/icon.png)")

img = Image.open(SRC).convert("RGBA")

# Se não for quadrado, corta centralizado (pra não deformar)
w, h = img.size
if w != h:
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))

OUT.parent.mkdir(parents=True, exist_ok=True)

# Salva ICO multi-size (o Windows escolhe o melhor tamanho automaticamente)
img.save(OUT, format="ICO", sizes=SIZES, bitmap_format="png")

print(f"✅ Gerado: {OUT} com tamanhos {', '.join([str(s[0]) for s in SIZES])}")
