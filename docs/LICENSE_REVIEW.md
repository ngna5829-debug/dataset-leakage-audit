# License and data review

Code/documentation: MIT. DATA_CARD.md already dedicates the original `build_demo()`
media and constructed relationship annotations under CC0-1.0; DATA_LICENSE.txt
now makes that existing scope explicit. Imported user media are excluded.

The inspected generator draws images with Pillow and creates videos with OpenCV.
A sample of six source images showed geometric drawings. Manifest and construction
truth records identify controlled fixtures, including intentionally incomplete
source claims; fixture review records must not be called human ratings. No
third-party photo/video corpus or model checkpoint was identified in the examples.

Dependencies are installed separately under their own licenses; this audit does
not apply software MIT to them. The text scan found no personal home paths,
email-like strings or secret-pattern matches in the inspected repository. This
is a detection result, not a guarantee that arbitrary future imported data is safe.

## Inspection scope

The 2026-10-23 review inspected the repository file tree, software LICENSE,
example manifests/provenance, relevant fixture or adapter code, source links and
license declarations. It searched common pretrained-weight extensions
(`.safetensors`, `.ckpt`, `.pth`, `.pt`, `.bin`, `.onnx`, `.h5`, `.gguf`), files over
20 MB, symlinks, obvious private-key/token filenames and text patterns, personal
home paths and email-like strings. No model-weight files, large unexplained
single files or symlinks were found in the inspected five-repository snapshot.
Installed environments and the external model cache are outside these repositories
and outside the publication target.

This is a snapshot review during concurrent work. It does not establish the
contents of a later Git commit/archive or prove that every upstream author owns
every right. The public file list must still be checked after packaging. No
source-code similarity audit or copyright clearance of training data was performed.
