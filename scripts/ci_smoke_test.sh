#!/bin/sh
set -u

URL="http://modular-monolith:5002/health"
TENTATIVAS=20
INTERVALO=3
i=1

echo "-> a verificar $URL ..."
while [ "$i" -le "$TENTATIVAS" ]; do
  if curl -sf "$URL" | grep -q '"stock"'; then
    echo "   OK: monólito e módulos carregados"
    exit 0
  fi
  i=$((i + 1))
  sleep "$INTERVALO"
done

echo "Validação falhou — consulte os logs de modular-monolith."
exit 1
