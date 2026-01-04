---
description: Keep instructions up-to-date
mode: subagent
model: opencode/kimi-k2
---

## Overview

Always keep documentation files in `AGENTS.md` and `instructions/` directory up to date with any changes made to the codebase.

**IMPORTANT**: At the start of each session, automatically check for new markdown files in the `instructions/` directory and update the list below if any are found.

## Documentation Files

Current documentation structure:

```
├── AGENTS.md                              # Coding standards + doc references ⭐
├── instructions/
│   ├── README.md                          # Instructions overview
│   ├── SESSION_IMPROVEMENTS.md            # Detailed changelog (36 items) ⭐
│   ├── QUICK_REFERENCE.md                 # Code examples & troubleshooting ⭐
│   ├── MIGRATIONS.md                      # Database migration guide ⭐
│   ├── TURSO_MIGRATION.md                 # Turso database setup guide
│   └── TURSO_CHANGES.md                   # Turso-specific changes log
```

## Update Guidelines

When making changes:
- **First**: Check `instructions/` directory for new markdown files not listed above
- Update **instructions/SESSION_IMPROVEMENTS.md** with detailed changelog entries
- Update **instructions/QUICK_REFERENCE.md** with new code examples if adding features
- Update **AGENTS.md** if coding standards or architecture patterns change
- Update **instructions/MIGRATIONS.md** if database schema changes
- Update **instructions/TURSO_MIGRATION.md** or **TURSO_CHANGES.md** if Turso-related changes
- Update **instructions/README.md** if instructions overview changes
- Update this file if new documentation files are added to `instructions/`