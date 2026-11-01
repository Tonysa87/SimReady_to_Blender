# Ricerca: da MDL (OmniPBR/OmniGlass) a materiali Blender

Data: 1/11/2026. Base: ricerca web estesa + sorgenti di Blender 5.2 + ricognizione di 6 asset reali.

## Conclusione

Per i materiali la strada è un **`USDHook` di Blender** (API ufficiale, Blender ≥ 4.4) registrato nel
processo di conversione headless. Il gancio intercetta i materiali OmniPBR/OmniGlass e costruisce un
Principled BSDF con i parametri MDL letti tramite `pxr`. Per tutto il resto (geometria, UV, istanze,
scala) resta l'importer USD nativo. Niente dipendenze esterne.

Strade scartate:

- **OSL**: funziona solo in Cycles (CPU e OptiX), **non in EEVEE**, con limitazioni in OptiX, e non
  esiste un traduttore MDL→OSL (la toolchain esistente va da MaterialX a OSL, non da MDL). Inoltre un
  materiale OSL non si esporta in altri formati.
- **MDL SDK Distiller** (open source, BSD-3): riduce qualsiasi MDL a un modello PBR e "cuoce" le
  texture. È potente ma pesante (libreria nativa, cuocitura per asset), e un forum segnala mappature
  di canali sbagliate proprio su OmniPBR. Non serve: gli asset usano solo due template noti
  (OmniPBR e OmniGlass) con parametri documentati, quindi una mappatura diretta è più fedele e
  deterministica.
- **Universal Material Mapper (NVIDIA)**: converte OmniPBR/OmniGlass all'import, ma esiste solo nel
  Blender di Omniverse (non nel Blender ufficiale) e non risulta codice né licenza pubblici.
- **Mappatura per nome delle texture**: inaffidabile (nomi incoerenti nel dataset).

Direzione futura da tenere d'occhio: NVIDIA pubblica materiali SimReady in **MaterialX/OpenPBR 1.1**
(repo `PhysicalAI-SimReady-Materials`, MIT-0), e Blender ha il supporto OpenPBR "in development"
nella roadmap. Se il dataset migrasse, l'importer USD di Blender legge già reti MaterialX e lo
stesso `USDHook` potrebbe gestirle.

## L'API `USDHook` (verificata nei sorgenti 5.2)

- `material_import_poll(import_context, usd_material) -> bool`: se `True`, Blender **salta**
  l'import UsdPreviewSurface/MaterialX per quel materiale e chiama:
- `on_material_import(import_context, bl_material, usd_material) -> bool`: riceve un materiale
  Blender con il node tree vuoto da riempire.
- `import_context.get_stage()`, `import_context.import_texture(asset_path) -> (path, is_temporary)`
  (con import in modalità Packed restituisce un file temporaneo da impacchettare).
- `on_import(import_context)`: dopo l'import, con la mappa prim USD → ID Blender (utile per
  marcare asset, metadati, varianti, pulizia delle prim di fisica).
- Esempio ufficiale: `doc/python_api/examples/bpy.types.USDHook.0.py`.

## Mappatura OmniPBR → Principled BSDF (da verificare sugli asset reali)

Parametri dalla documentazione OmniPBR (nome MDL → default):

| OmniPBR | Default | Blender |
|---|---|---|
| `diffuse_texture` × `diffuse_tint` | tint 1,1,1 | Image (sRGB) × RGB → Base Color (il preview USD **perde** la tinta) |
| `diffuse_color_constant` | 0.2 grigio | Base Color se non c'è texture |
| `albedo_brightness/add/desaturation` | 1/0/0 | nodi Math/Hue-Sat se ≠ default |
| `enable_ORM_texture`, `ORM_texture` | false | Separate Color: R=AO, G=Roughness, B=Metallic (confermato dai dati) |
| `reflection_roughness_constant`, `…_texture_influence` | 0.5, 0 | mix(costante, mappa, influenza) |
| `metallic_constant`, `metallic_texture_influence` | 0, 0 | mix(costante, mappa, influenza) |
| `reflectionroughness_texture`, `metallic_texture` | — | mappe separate (es. occhiali, ORM disattivato) |
| `specular_level` | 0.5 | Specular IOR Level |
| `normalmap_texture`, `bump_factor` | 1.0 | Normal Map (strength); vedi sotto per il verso |
| `flip_tangent_u/v` | false/true | verso della normal map (vedi sotto) |
| `enable_emission`, `emissive_color(_texture)`, `emissive_intensity` | false | Emission Color/Strength |
| `enable_opacity`, `opacity_constant/texture`, `opacity_threshold` | false | Alpha (+ blend/clip) |
| `enable_clearcoat`, `clearcoat_*` | false | Coat |
| `texture_scale/translate/rotate` | — | Mapping node |

OmniGlass (`glass_color`, `glass_ior`, `cutout_opacity`, `roughness_texture`,
`normal_map_strength`) → Principled con Transmission Weight 1, IOR, colore e roughness
(oggi il preview USD lo rende opaco).

## Punto aperto: verso della normal map

Le fonti si contraddicono:
- la documentazione Omniverse: "OmniPBR è impostato per normal map DirectX (`flip_tangent_v=true`)";
- le linee guida dei materiali SimReady: normal map DirectX;
- il progetto cramera (PR #91): nei pacchetti SimReady la stessa normal map è usata da OmniPBR e da
  UsdPreviewSurface, ed è in convenzione glTF (OpenGL).

→ **Si decide con un test visivo** nella fase 2: render della stessa luce radente con e senza
inversione del canale verde, confrontato con l'anteprima NVIDIA.

## Fonti

- OmniPBR, parametri: https://docs.omniverse.nvidia.com/materials-and-rendering/latest/templates/OmniPBR.html
- OmniGlass: https://docs.omniverse.nvidia.com/materials-and-rendering/latest/templates/OmniGlass.html
- Universal Material Mapper: https://docs-prod.omniverse.nvidia.com/connect/latest/blender/material-mapper.html
- MDL SDK distilling: https://raytracing-docs.nvidia.com/mdl/api/mi_neuray_example_distilling.html
- Forum, mappatura di canali sbagliata in OmniPBR: https://forums.developer.nvidia.com/t/need-clarification-on-the-example-distilling-cpp-code-for-reflection-roughness-texture/340059
- MDL open source: https://developer.nvidia.com/blog/nvidia-announces-full-open-source-of-material-definition-language-to-streamline-graphics-pipelines/
- Blender roadmap (OpenPBR): https://www.blender.org/roadmap/
- OpenPBR in Blender: https://projects.blender.org/blender/blender/issues/156437 , https://projects.blender.org/blender/blender/issues/156393
- OSL in Blender 5.2: https://docs.blender.org/manual/en/5.2/render/cycles/osl/index.html
- USDHook: https://docs.blender.org/api/current/bpy.types.USDHook.html
- Materiali SimReady MaterialX/OpenPBR: https://github.com/NVIDIA-Omniverse/PhysicalAI-SimReady-Materials
- cramera, materiali SimReady: https://github.com/AICOR-Solutions/cramera/pull/91
- Forum, subIdentifier OmniPBR all'import in Blender: https://forums.developer.nvidia.com/t/blender-import-usd-not-importing-materials/194041
