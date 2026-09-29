# buscavagas

Script em Python que busca vagas de emprego em vários sites ao mesmo tempo e
junta tudo numa lista única, focado em vagas remotas de nível inicial e
vagas na região de Januária-MG.

## O que faz

- Consulta 6 fontes em paralelo: Gupy, InfoJobs, LinkedIn (busca pública),
  Remotar, vagas.com.br e repositórios de vagas de TI no GitHub.
- Filtra vagas de entrada (júnior, estágio, trainee, assistente, "sem
  experiência") e descarta pleno/sênior/gerência.
- Remove duplicatas: a mesma vaga vinda de sites diferentes aparece uma vez só,
  indicando em quantas fontes foi encontrada.
- Ignora vagas antigas, anúncios de "renda extra/comissão" e "banco de talentos".
- Marca com estrela as vagas cujo anúncio diz que não exige experiência.
- Salva o resultado numa planilha CSV e mostra na tela.
- Lembra o que já foi mostrado, para --novas exibir só o que apareceu depois.

## Uso

    python3 buscar_vagas.py                 # busca tudo (~3 min)
    python3 buscar_vagas.py --novas         # só o que ainda não tinha aparecido
    python3 buscar_vagas.py --area ti       # ti | adm | outros
    python3 buscar_vagas.py --dias 30       # só vagas dos últimos 30 dias
    python3 buscar_vagas.py --fontes infojobs,linkedin   # escolhe as fontes
    python3 buscar_vagas.py --banco         # inclui "banco de talentos"

O resultado sai no terminal e numa planilha vagas_AAAA-MM-DD_HHMM.csv na pasta
do projeto (colunas: nova, área, modo, vaga, empresa, sem_exp, publicada, fonte,
repetições, link).

## Detalhes técnicos

- Só usa a biblioteca padrão do Python (nenhuma dependência externa).
- Força IPv4, porque a rede local não alcança essas APIs por IPv6 e o timeout
  travava a busca.
- InfoJobs e LinkedIn são lidos direto do HTML das páginas (não têm API aberta),
  então mudanças de layout desses sites podem exigir ajuste.
