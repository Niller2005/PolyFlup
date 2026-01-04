---
description: Keep instructions up-to-date
mode: subagent
model: opencode/claude-sonnet-4.5
---

Always keep the following documentation files up to date with any changes made to the codebase:

```
├── README.md                    # Project overview
├── AGENTS.md                    # Coding standards + doc references ⭐
├── SESSION_IMPROVEMENTS.md      # Detailed changelog (36 items) ⭐
├── QUICK_REFERENCE.md           # Code examples & troubleshooting ⭐
└── MIGRATIONS.md                # Database migration guide ⭐
```

When making changes:
- Update **SESSION_IMPROVEMENTS.md** with detailed changelog entries
- Update **QUICK_REFERENCE.md** with new code examples if adding features
- Update **AGENTS.md** if coding standards or architecture patterns change
- Update **MIGRATIONS.md** if database schema changes
- Update **README.md** if project overview or setup instructions change