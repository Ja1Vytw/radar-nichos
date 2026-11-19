# Radar de Nichos

Site estático que lista canais novos (primeiro vídeo recente) que já estão performando bem, por nicho e idioma. Sem banco de dados: os dados ficam em `site/data.js` e são atualizados uma vez por mês, sozinhos, pelo GitHub Actions.

## Estrutura

```
collector/config.json   nichos, buscas por idioma e filtros
collector/collect.py    coletor (YouTube Data API v3)
data/AAAA-MM.json       um snapshot por mês (histórico)
site/index.html         o site
site/data.js            dados que o site lê (gerado pelo coletor)
.github/workflows/      atualização automática todo dia 1
```

Enquanto o coletor não rodar, o site mostra **dados de exemplo fictícios**, com um aviso no topo.

## Configuração (uma vez)

1. **Chave da API (grátis):** no Google Cloud Console, crie um projeto, ative a "YouTube Data API v3" e crie uma chave de API.
2. **Repositório:** suba esta pasta para um repositório no GitHub.
3. **Segredo:** no repositório, vá em Settings → Secrets and variables → Actions e crie `YT_API_KEY` com a chave.
4. **Site:** em Settings → Pages, escolha "GitHub Actions" como fonte.
5. **Primeira coleta:** em Actions → "Atualizar radar de nichos" → Run workflow.

A partir daí, todo dia 1 o radar é atualizado e publicado sozinho.

## Rodar no seu computador

```
YT_API_KEY=sua_chave python collector/collect.py
```

Depois, é só abrir `site/index.html` no navegador.

## Ajustar nichos e filtros

Edite `collector/config.json`:

- `niches`: cada nicho tem um nome, um formato (`long` ou `shorts`) e uma busca por idioma.
- `max_channel_age_days`: idade máxima contada a partir do **primeiro vídeo**, não da criação do canal (padrão: 60).
- `min_avg_views_long` / `min_avg_views_shorts`: média mínima de views por vídeo.
- `lookback_days`: janela da busca por vídeos em alta (padrão: 30).

## Cota da API

A cota gratuita é de 10.000 unidades por dia. Cada busca custa 100 unidades, então a configuração atual (4 nichos × 5 idiomas) usa cerca de 2.100 unidades por coleta. Dá pra chegar a uns 90 pares de nicho × idioma por dia.

## Como a nota funciona

A nota é calculada a partir das views por dia desde o primeiro vídeo, comparadas com o melhor canal do mesmo formato naquele mês (raiz quadrada, de 0 a 100). Ela serve pra ordenar e comparar canais, não é uma previsão de resultado.
