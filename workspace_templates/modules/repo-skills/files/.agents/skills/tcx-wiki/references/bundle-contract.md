# Knowledge Wiki contracts

## Local page

Store pages below `wikis/local/pages/` as UTF-8 Markdown. Use free-form body
text and this minimal frontmatter:

```yaml
---
title: EUV Photoresist
type: technology
summary: Short routing summary.
aliases:
  - Extreme ultraviolet photoresist
tags:
  - semiconductor
status: current
updated_at: 2026-07-19
sources:
  - https://example.org/public-source
---
```

Use a kebab-case `type`. Recommended values are `company`, `product`,
`technology`, `material`, `process`, `concept`, `value-chain`, `comparison`,
and `synthesis`. Status is `draft`, `current`, `contested`, or `superseded`.
Local sources may additionally use `artifact:<id>` or
`file:<workspace-relative-path>` as soft notes.

## Shareable bundle

```text
wiki-packages/knowledge-wiki-example/
├── .tradingcodex/plugin.json
└── knowledge-wiki-example/
    ├── purpose.md
    ├── index.md
    └── pages/**/*.md
```

The manifest is strict:

```json
{
  "format": "tradingcodex.knowledge-wiki",
  "schema_version": 1,
  "type": "knowledge-wiki",
  "id": "knowledge-wiki-example",
  "version": "1.0.0",
  "wiki": "knowledge-wiki-example",
  "source": {
    "publisher": "User-selected publisher",
    "repository": "https://example.org/public/repository",
    "license": "User-selected license"
  }
}
```

The bundle may contain only the manifest and Markdown files. Every page under
`pages/` needs at least one portable public HTTPS URL, `doi:`, `standard:`, or
`patent:` source. Do not include scripts, symlinks, raw documents, assets,
credentials, local paths, or `artifact:`/`file:` identifiers. Shared wikilinks
must be canonical internal links such as
`[[knowledge-wiki-example/pages/euv-photoresist]]`, and the target must exist.
