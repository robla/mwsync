# Migration Guide: Legacy `mwsync` to `mwmap`

This document describes the transition from the legacy `_cache` infrastructure used by `mwsync.py` to the new `_mwmap` structure introduced in `mwmap`.

## Infrastructure Changes

| Feature | Legacy (`mwsync.py`) | New (`mwmap.py`) |
| :--- | :--- | :--- |
| **Metadata Directory** | `_cache/` | `_mwmap/` (may be renamed to `_mwsync/`) |
| **Configuration** | Often implicit or in `_cache/` | Explicit `_mwmap/config.yaml` |
| **Mapping Model** | 1:1 page-to-file sync | Multi-format mapping (page, tree, wiki) |
| **Cache Storage** | Mixed metadata and bodies | Dedicated `_mwmap/cache/` directory |

## Migration Strategy

### 1. Structure Transition
The legacy `_cache` directory served as a combined metadata and content store. `mwmap` separates these concerns:
- `_mwmap/config.yaml`: Durable, user-edited configuration of remotes and mapping rules.
- `_mwmap/cache/`: Disposable, system-generated cache of remote metadata and page content.

### 2. Explicit Mappings
While `mwsync` often relied on implicit 1:1 relationships, `mwmap` requires explicit mapping definitions. This allows for more complex relationships, such as mapping an entire MediaWiki namespace to a local folder or a Zim notebook subtree.

### 3. Renaming Note
As of June 2026, the metadata directory is named `_mwmap`. However, this may be renamed to `_mwsync` if `mwmap` is officially adopted as the next generation of `mwsync`. Migration tools and documentation will be updated accordingly if this change occurs.

## Manual Migration Steps (Draft)

1. **Initialize**: Run `mwmap init` to create the new metadata structure.
2. **Register Remotes**: Use `mwmap remote add` to define the MediaWiki instances previously managed by `mwsync`. The local working tree is the `--root` directory itself and is not registered.
3. **Define Mappings**: Manually add mapping entries to `_mwmap/config.yaml` that replicate your existing `mwsync` setup.
4. **Verification**: Run `mwmap status` to ensure the new mapping logic correctly identifies the existing local files.
