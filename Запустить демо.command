#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/mipt-auto ]]; then
  print 'Сначала установите зависимости по README.md.'
  read '?Нажмите Enter для выхода: '
  exit 1
fi
.venv/bin/mipt-auto demo --channel chrome --keep-open
