# Translations

[Back to Docking](../README.md)

Run the commands below from a source checkout; see the
[development guide](DEVELOPMENT.md) for setup and contribution steps.
Package compilation and ownership are documented in the
[packaging guide](../packaging/README.md#translation-ownership).

Docking now ships 74 locale catalogs via standard gettext (plus English fallback).

Core locales include:

| Language | Code |
|----------|------|
| Brazilian Portuguese | pt_BR |
| Spanish | es |
| French | fr |
| Simplified Chinese | zh_CN |
| Hindi | hi |
| Arabic | ar |
| German | de |
| Japanese | ja |
| Korean | ko |
| Russian | ru |

Additional locales are available under `docking/locale/*/LC_MESSAGES/docking.po`.

The dock automatically uses your system locale. To test a specific language:

```bash
LANGUAGE=pt_BR python run.py
```

## Adding a new translation

Create a catalog from the template, edit it with a PO editor such as Poedit or
Lokalize, run the validation workflow below, and submit the `.po` file in a pull
request.

```bash
msginit --input=docking/locale/docking.pot \
  --locale=XX \
  --output=docking/locale/XX/LC_MESSAGES/docking.po
```

## Changing translatable strings

After adding or modifying a user-visible `_("...")` string, regenerate the
template and run the same checks as CI:

```bash
./tools/i18n.sh --extract
./tools/i18n.sh --check-pot-sync
./tools/i18n.sh --check-catalogs --allow-incomplete
./tools/i18n.sh --compile
```

Regular feature commits only update `docking/locale/docking.pot`; they do not
need to refresh every catalog or fill every new `msgstr`.

## Translation maintenance

Translation-only updates merge the current template into every catalog and can
apply the stricter completeness check:

```bash
./tools/i18n.sh --update-translations
./tools/i18n.sh --check-catalogs --require-complete
```
