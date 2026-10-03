# Legwork public cache

Wrappers here are used by `legwork owner/repo` without a model call, so
these 37 repos build with no API key. Every entry was reviewed by hand and
tried with a real call before it was merged, and each is still scanned, installed and
self-tested on your machine before it's used.

| Repo | What the wrapper does | Install | License |
|---|---|---|---|
| [adbar/trafilatura](https://github.com/adbar/trafilatura) | Extract main text and metadata from HTML strings or local HTML files using Trafilatura | `pip install trafilatura` | Apache-2.0 |
| [ast-grep/ast-grep](https://github.com/ast-grep/ast-grep) | Run the ast-grep CLI for AST-based code search and rewrite | `pip install ast-grep-cli` | MIT |
| [astral-sh/ruff](https://github.com/astral-sh/ruff) | Ruff CLI to lint (check) and format Python code | `pip install ruff` | MIT |
| [collective/icalendar](https://github.com/collective/icalendar) | Parse and generate iCalendar (.ics) files: list events from ICS text or file, and create/append events to ICS files | `pip install icalendar` | BSD-2-Clause |
| [danielgatis/rembg](https://github.com/danielgatis/rembg) | Remove image backgrounds from local images using rembg’s Python API (models cached under ./models) | `pip install "rembg[cpu,cli]"` + model/data download | MIT |
| [duckdb/duckdb](https://github.com/duckdb/duckdb) | Execute SQL queries with DuckDB (in-memory or file-backed) and return results | `pip install duckdb` | MIT |
| [gitleaks/gitleaks](https://github.com/gitleaks/gitleaks) | Scan directories, git repositories, or stdin for secrets using the Gitleaks CLI and return findings as JSON | platform-specific binary download | MIT |
| [hgrecco/pint](https://github.com/hgrecco/pint) | Convert quantities between units using the Pint Python library (exposes conversion and conversion-factor tools) | `pip install pint` | BSD-2-Clause |
| [jdepoix/youtube-transcript-api](https://github.com/jdepoix/youtube-transcript-api) | Retrieve, list, and translate YouTube video transcripts using the youtube-transcript-api library | `pip install youtube-transcript-api` | MIT |
| [joke2k/faker](https://github.com/joke2k/faker) | Generate arbitrary fake data fields by provider name via the Faker Python API, with optional locales, seeding, repeats, and custom providers | `pip install Faker` | MIT |
| [jsvine/pdfplumber](https://github.com/jsvine/pdfplumber) | Extract text, tables, and low-level page objects from local PDFs using pdfplumber’s Python API | `pip install pdfplumber` | MIT |
| [jupyter/nbconvert](https://github.com/jupyter/nbconvert) | Convert Jupyter .ipynb notebooks to static formats (e.g., HTML, Markdown) via nbconvert’s Python API | `pip install nbconvert` | BSD-3-Clause |
| [koalaman/shellcheck](https://github.com/koalaman/shellcheck) | Run ShellCheck to lint shell scripts (from text or files) and return structured diagnostics | platform-specific binary download | AGPL-3.0 |
| [lincolnloop/python-qrcode](https://github.com/lincolnloop/python-qrcode) | Generate QR codes as PNG or SVG and ASCII art from text using the python-qrcode library | `pip install "qrcode[pil]"` | BSD-2-Clause |
| [lovell/sharp](https://github.com/lovell/sharp) | Image resizing/format conversion and metadata via the Node sharp library invoked from Python | `npm install sharp` | Apache-2.0 |
| [matplotlib/matplotlib](https://github.com/matplotlib/matplotlib) | Generate static plots (line, scatter, histogram) with Matplotlib and return PNG images | `pip install matplotlib` | none |
| [matthewwithanm/python-markdownify](https://github.com/matthewwithanm/python-markdownify) | Convert HTML (string or file) to Markdown using the python-markdownify library | `pip install markdownify` | MIT |
| [microsoft/markitdown](https://github.com/microsoft/markitdown) | Convert local files or URLs to Markdown using the MarkItDown Python API, plus a tool to list available plugins | `pip install "markitdown[all]"` | MIT |
| [mikefarah/yq](https://github.com/mikefarah/yq) | Run yq to query and transform YAML/JSON/XML/INI/etc via jq-like expressions (evaluate expressions against files or stdin, and convert formats) | platform-specific binary download | MIT |
| [plotly/plotly.py](https://github.com/plotly/plotly.py) | Generate interactive Plotly bar charts as HTML from x/y data using plotly.graph_objects | `pip install plotly` | MIT |
| [prettier/prettier](https://github.com/prettier/prettier) | Run Prettier’s CLI to format code (format from string via stdin or operate on files) using ./node_modules/.bin/prettier | `npm install prettier` | MIT |
| [py-pdf/pypdf](https://github.com/py-pdf/pypdf) | PDF text extraction, metadata reading, splitting, and merging using pypdf | `pip install pypdf[crypto]` | BSD-2-Clause |
| [python-jsonschema/jsonschema](https://github.com/python-jsonschema/jsonschema) | Validate JSON data against JSON Schema using the jsonschema Python library | `pip install "jsonschema[format]"` | MIT |
| [python-openxml/python-docx](https://github.com/python-openxml/python-docx) | Create, read, and modify .docx files using python-docx (create new documents, extract paragraphs, and append text into a new copy) | `pip install python-docx` | MIT |
| [python-pillow/Pillow](https://github.com/python-pillow/Pillow) | Perform common image operations (inspect, resize, rotate, convert, crop, grayscale, overlay text) via Pillow's Python API | `pip install pillow` | unrecognized (LICENSE) |
| [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) | Offline OCR on local images using RapidOCR (ONNX Runtime), with optional visualization output | `pip install rapidocr onnxruntime` + model/data download | Apache-2.0 |
| [scanny/python-pptx](https://github.com/scanny/python-pptx) | Create, update, and extract text from PowerPoint (.pptx) files using the python-pptx library | `pip install python-pptx` | MIT |
| [simonw/sqlite-utils](https://github.com/simonw/sqlite-utils) | Run SQLite queries and modify tables using the sqlite-utils Python library | `pip install sqlite-utils` | Apache-2.0 |
| [sqlfluff/sqlfluff](https://github.com/sqlfluff/sqlfluff) | Lint SQL strings using the sqlfluff CLI (and report the installed sqlfluff version) | `pip install sqlfluff` | MIT |
| [svg/svgo](https://github.com/svg/svgo) | Optimize SVG files using the local svgo CLI (minify a file or an SVG string) | `npm install svgo` | MIT |
| [sympy/sympy](https://github.com/sympy/sympy) | Symbolic math operations via SymPy’s Python API (simplify, differentiate, integrate, solve, series, LaTeX) | `pip install sympy` | MIT |
| [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Offline speech-to-text transcription using Faster-Whisper with a pre-downloaded tiny.en CTranslate2 model | `pip install "faster-whisper" "av>=10.0.0" numpy huggingface_hub` + model/data download | MIT |
| [textstat/textstat](https://github.com/textstat/textstat) | Compute readability metrics and text statistics from text using the textstat Python library | `pip install textstat` + model/data download | MIT |
| [wireservice/csvkit](https://github.com/wireservice/csvkit) | Run csvkit CLI tools (csvcut, csvstat, in2csv, etc.) via a generic subprocess wrapper | `pip install csvkit` | MIT |
| [xhtml2pdf/xhtml2pdf](https://github.com/xhtml2pdf/xhtml2pdf) | Convert HTML (string or local .html file) to a PDF using xhtml2pdf’s Python API | `pip install xhtml2pdf` | Apache-2.0 |
| [yt-dlp/yt-dlp](https://github.com/yt-dlp/yt-dlp) | Use yt-dlp to download media or extract info from URLs via its Python API | `pip install "yt-dlp[default]"` | Unlicense |
| [Zulko/moviepy](https://github.com/Zulko/moviepy) | Trim and transcode local video files using MoviePy (FFmpeg pre-downloaded for offline use) | `pip install moviepy` + model/data download | MIT |

## Format

One folder per repo, `<owner>__<repo>` in lowercase:

- `wrapper.py`: the MCP wrapper, with its self-test
- `manifest.json`: source repo and commit, synthesis date and model,
  source license and any license flag, smoke-test result, install command

## Adding one

Run `legwork contribute owner/repo` in your fork and open a PR; see
[CONTRIBUTING.md](../CONTRIBUTING.md). A bad entry is removed with a plain
`git revert`.
