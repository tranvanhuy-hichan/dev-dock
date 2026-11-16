# Contributing to DevDock

Thanks for your interest! Bug reports, ideas, docs, translations and code are all welcome.

## Development setup

```bash
git clone https://github.com/tranvanhuy-hichan/dev-dock.git && cd dev-dock
./bin/devdock            # run from the source tree (needs Python 3.11+, GTK4, libadwaita)
```

## Workflow

1. Open an [issue](https://github.com/tranvanhuy-hichan/dev-dock/issues) first for larger changes. Look for `good first issue` / `help wanted` labels if you are new.
2. Fork, then branch from `main` (`feat/...`, `fix/...`, `docs/...`).
3. Keep changes focused and update the README when behaviour changes.
4. Before opening a PR, run:
   ```bash
   python3 -m unittest discover -s tests -v
   ```
5. Use conventional commit prefixes (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) and describe *what* and *why* in the PR.

## License

By contributing you agree that your contributions are licensed under the project's [MIT License](LICENSE).

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md). For vulnerabilities see [SECURITY.md](SECURITY.md).
