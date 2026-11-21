# Hosted BEQ catalogue

The Remote no longer downloads the upstream 39 MB catalogue JSON at startup.
A scheduled GitHub Actions workflow fetches the upstream database, validates
and transforms it, and publishes a compact static dataset to GitHub Pages.

The hosted layout is versioned:

- `beq/manifest.json` points to an immutable version directory.
- `beq/<version>/index.json` contains only the media ID, original title, author, year, audio types, and compact integer category offsets. Search matches the original title or author; the display name is constructed from title and author so different contributors’ versions remain distinguishable. Category offsets avoid storing category labels redundantly on every entry.
- `beq/<version>/entries/<id>.json` contains only the filter data needed to
  apply that selected BEQ.
- `beq/filterless-entries.txt` and `beq/filterless-entries.json` are
  downloadable reports of upstream records with no filters. These records are
  excluded from the Remote browse/search index and are not published as entry
  payloads, because they cannot be applied to the HTP-1.
- `beq/unsupported-entries.txt` and `beq/unsupported-entries.json` report
  records that the integration cannot apply: entries with a filter type that
  has no protocol mapping, or more than 16 filters. The HTP-1 PEQ UI documents
  seven filter types, but the current integration maps PeakingEQ, LowShelf,
  and HighShelf. Other types are an integration limitation, not a claim about
  hardware capability.
- `beq/adjusted-entries.txt` and `beq/adjusted-entries.json` list filters
  whose numeric values were normalized to the HTP-1 BEQ application limits.
  This follows the current HTP-1 native controller's BEQ handling (frequency
  10–20,000 Hz, gain −99 to +20 dB, Q 0.1–10), rather than rejecting valid
  catalogue entries because they exceed the narrower PEQ editor input ranges.
  The original filter values remain part of the legacy media-ID calculation.
  Malformed or non-finite source values still fail the build.

BEQ application requires PEQ Pre placement. Pre PEQ operates on the
decoded LFE channel before bass management and Dirac Live, not on individual
subwoofer speaker outputs. This is also why Pre placement is compatible with
Dirac calibration: the measurement signal is injected at the Dirac stage and
does not pass through Pre PEQ. The integration therefore writes the BEQ filter
set only to available zero-gain bands on the LFE PEQ channel. It does not
duplicate the filters across physical subwoofer channels. Post placement is for
speaker/output correction and cannot be used for BEQ.

The current Bass EQ guide still describes writing filters to subwoofer channels,
which conflicts with the PEQ Placement and revised signal-flow documentation.
This implementation follows the latter's explicit description of the current
Pre signal path. Confirm the exact LFE channel key and resulting behavior on
the target firmware during the planned HTP-1 device test before deployment.

When clearing or replacing a BEQ, the integration resets only BEQ-marked bands;
it does not restore their previous values, alter other PEQ bands, or switch PEQ
off. Cleanup also recognizes legacy BEQ markers on subwoofer channels created
by earlier integration versions, but new filters are never written to those
channels.

The integration verifies the index SHA-256 before using it and fetches an
individual entry only when selected. It keeps a bounded 12-entry in-memory
LRU cache. Browse and search never download filter payloads. The integration
does not prefetch the catalogue on connection.

## Publishing

The `Publish BEQ catalogue` workflow runs daily and can be started manually.
Before the first publication, enable GitHub Pages in repository Settings →
Pages and set the build and deployment source to **GitHub Actions**. The
`actions/configure-pages` step reads the Pages configuration; it does not
enable Pages on a repository where Pages is disabled. The workflow deploys
generated static files; Pages itself does not execute refresh logic.

The manifest is published with the immutable versioned data in the same Pages
deployment, so clients cannot combine an index and entry files from different
catalogue versions. It includes counts and report paths for filterless, integration-unsupported,
and value-adjusted entries. A failed fetch, validation, or transform prevents deployment.

## Upstream data and attribution

The generated data is derived from the BEQCatalogue database and BEQ filter
records maintained by Matt Khan in:

- https://github.com/3ll3d00d/beqcatalogue
- https://github.com/3ll3d00d/beqfilters

Both upstream projects provide MIT license notices. The publishing workflow
copies those notices alongside the generated catalogue. The integration's
own source code remains under its existing MPL-2.0 license.
