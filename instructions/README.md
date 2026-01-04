# Instructions & Documentation

This folder contains internal documentation and instructions for PolyAstra development.

## Files

### Feature Documentation
- **SESSION_IMPROVEMENTS.md** - Complete changelog of improvements and new features
- **QUICK_REFERENCE.md** - Quick reference guide for new features and functions

### Database
- **MIGRATIONS.md** - Database migration system guide
- **TURSO_MIGRATION.md** - Complete setup guide for Turso database
- **TURSO_CHANGES.md** - Technical summary of Turso migration changes

## Root-Level Documentation

The following files are kept in the project root:
- **AGENTS.md** (root) - Agent-specific development guidelines and coding standards
- **README.md** (root) - Main project README

## Usage

These files are automatically loaded by OpenCode via `opencode.json`:

```json
{
  "instructions": [
    "AGENTS.md",
    "instructions/*.md"
  ]
}
```

## Adding New Documentation

1. Create a new `.md` file in this folder
2. It will automatically be picked up by OpenCode
3. No need to update `opencode.json`

## File Organization

- **Root**: AGENTS.md (guidelines), README.md (project docs)
- **instructions/**: Feature docs, database guides, migration instructions
- **Index**: This file (README.md)
