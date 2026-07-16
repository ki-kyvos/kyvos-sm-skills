# Contributing to kyvos-sm-skills

Thank you for your interest in contributing! Please follow these guidelines.

## Development Setup

```bash
git clone https://github.com/<ORG>/kyvos-sm-skills.git
cd kyvos-sm-skills
pip install -e ".[dev]"
```

## Code Style

- Python 3.11+
- Line length: 120 characters
- Use `ruff` for linting: `ruff check .`
- Use `mypy` for type checking: `mypy kyvos_sm_skills/`
- Follow existing code patterns in the codebase

## Testing

- All new features must include tests
- Target 90%+ coverage
- Run tests: `pytest --cov=kyvos_sm_skills --cov-report=term-missing`

## Pull Request Process

1. Fork the repo and create a feature branch
2. Write tests for your changes
3. Ensure CI passes: `ruff check . && mypy kyvos_sm_skills/ && pytest`
4. Submit a PR with a clear description of changes
5. Address review feedback

## Adding New Claude Skills

1. Create a markdown file in `skills/` following the existing format
2. Include: System Prompt, Input Schema, Output Schema, Example, Backend reference
3. Add a corresponding test
4. Update `docs/claude-skill-usage.md`

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
