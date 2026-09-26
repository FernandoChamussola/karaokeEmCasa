# 🎤 Karaoke em Casa

Carregas uma música normal. O programa separa a voz do instrumental, arranja a letra sincronizada e depois tocas o instrumental com a letra a acender palavra a palavra, no browser ou na TV.

## Como funciona

1. **Separação**: o [Demucs](https://github.com/facebookresearch/demucs) (Meta) divide a música em `vocals.mp3` e `instrumental.mp3`.
2. **Letra**:
   - primeiro procura no [LRCLIB](https://lrclib.net), uma base de dados gratuita de letras já sincronizadas. Se encontrar, a letra fica perfeita;
   - se não encontrar, transcreve a voz isolada com o [faster-whisper](https://github.com/SYSTRAN/faster-whisper), com o tempo de cada palavra.
3. **Palco**: página web com a letra a encher palavra a palavra, voz guia opcional e ajuste do atraso da letra.

Cada música é processada **uma só vez** e fica guardada em `./data`.

## Downloads por link

Na aba **⬇️ Downloads**, cola um link do YouTube, SoundCloud ou de outro dos [muitos sites suportados](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md). O [yt-dlp](https://github.com/yt-dlp/yt-dlp) descarrega a música, extrai o áudio para mp3 e apaga o vídeo.

- Com **"Criar o karaoke automaticamente"** ligado, a música entra logo na fila do karaoke.
- Senão, fica na lista de downloads. Depois, na aba Músicas, escolhe **"⬇️ Dos downloads"** em vez de **"📁 Do dispositivo"**.
- O yt-dlp é atualizado sempre que o contentor arranca, porque o YouTube muda muitas vezes. Para desligar, define `YTDLP_AUTO_UPDATE: 0` no `docker-compose.yml`.

## Arrancar (local)

```bash
docker compose -f docker-compose.dev.yml up -d --build
```

Abre **http://localhost:8000**. Também podes abrir a partir do telemóvel ou da TV na mesma rede: http://IP-DO-PC:8000.

## Deploy (VPS com Portainer + Traefik)

O `docker-compose.yml` é o de produção: não expõe portas e publica a app em **https://kamusic.duckdns.org** através do Traefik (SSL automático).

1. Na VPS, cria a network uma vez: `docker network create traefik-public`
2. Confirma que o DuckDNS `kamusic` aponta para o IP da VPS.
3. No Portainer: **Stacks > Add Stack > Repository**, com o compose path `docker-compose.yml`.
   Variáveis opcionais: `WHISPER_MODEL` (padrão `medium`) e `DEMUCS_MODEL` (padrão `htdemucs`).

As músicas ficam no volume `karaoke-data` e os modelos no volume `models`.

O YouTube bloqueia downloads feitos a partir de IPs de datacenter ("confirm you're not a bot"). Por isso a stack de produção tem mais dois contentores, que funcionam sozinhos:

- `karaoke-warp`: Cloudflare WARP. Os downloads saem por ele, com um IP que o YouTube aceita.
- `karaoke-pot`: gera os "PO tokens" que o YouTube pede.

Plano B, se voltar a bloquear: abre `/?admin` e carrega um `cookies.txt` de uma conta do YouTube.

Na primeira música vai demorar mais, porque é preciso descarregar os modelos (cerca de 1,5 GB). Ficam guardados no volume `models`.

## Tempos (só CPU, sem placa gráfica)

Para uma música de 4 minutos:

| Passo | Tempo aproximado |
|---|---|
| Separar a voz (htdemucs) | 2 a 4 min |
| Letra do LRCLIB | segundos |
| Transcrição com Whisper `medium` | 3 a 6 min |

Para ir mais rápido, muda `WHISPER_MODEL` para `small` no `docker-compose.yml`. A letra piora um pouco.

## Dicas

- Nomeia os ficheiros como `Artista - Título.mp3`, ou preenche os campos. Assim aumenta a probabilidade de encontrar a letra no LRCLIB.
- Se a letra vier errada, carrega em **✎ Refazer letra** e corrige o artista e o título, ou força a transcrição com IA.
- Se a letra aparecer adiantada ou atrasada, usa **Letra − / +** no palco. O ajuste fica guardado para essa música.
- Teclas no palco: `Espaço` para tocar ou pausar, `←` e `→` para andar 5 s, `F` para ecrã inteiro e `Esc` para voltar.
- Clica numa linha da letra para saltar para essa parte.
