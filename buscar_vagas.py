#!/usr/bin/env python3
"""Busca vagas em vários sites: remotas de nível inicial + vagas perto de Januária-MG.

Fontes: Gupy, InfoJobs, LinkedIn (só região), Remotar (remotas),
vagas.com.br (só região) e repositórios de vagas de TI no GitHub.

Uso:
    python3 buscar_vagas.py                  # busca tudo
    python3 buscar_vagas.py --area ti        # só TI (ti | adm | outros)
    python3 buscar_vagas.py --novas          # mostra só as que não apareceram antes
    python3 buscar_vagas.py --dias 30        # só vagas publicadas nos últimos 30 dias
    python3 buscar_vagas.py --fontes gupy,infojobs
    python3 buscar_vagas.py --banco          # inclui "Banco de Talentos"
"""
import argparse
import csv
import html
import json
import re
import socket
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

# O IPv6 da rede local não alcança vários sites e o Python demora no timeout
# antes de tentar IPv4; então usamos só IPv4.
_getaddrinfo = socket.getaddrinfo
socket.getaddrinfo = lambda *a, **k: [r for r in _getaddrinfo(*a, **k)
                                      if r[0] == socket.AF_INET]

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
PASTA = Path(__file__).resolve().parent
VISTAS = PASTA / "vagas_vistas.json"

# Cidades "perto" (Januária e região / polos do norte de MG)
CIDADES_PERTO = [
    "Januária", "Pedras de Maria da Cruz", "Itacarambi", "São Francisco",
    "Bonito de Minas", "Cônego Marinho", "Manga", "Brasília de Minas",
    "Montes Claros", "Janaúba", "São João da Ponte", "Varzelândia",
    "Jaíba", "Matias Cardoso", "Miravânia", "São João das Missões",
    "Japonvar", "Coração de Jesus", "Luislândia", "Ubaí", "Icaraí de Minas",
]

# Palavras que indicam vaga de entrada / sem experiência
ENTRADA = [
    "junior", "jr", "estagio", "estagiario", "estagiaria", "trainee",
    "aprendiz", "jovem aprendiz", "assistente", "auxiliar", "atendente",
    "operador", "operadora", "iniciante", "primeiro emprego",
    "sem experiencia", "entry level", "recepcionista", "telemarketing",
    "sac", "bolsista", "ajudante", "repositor", "promotor",
]
# Palavras que indicam vaga que exige experiência
EXCLUIR = [
    "senior", "sr", "pleno", "pl", "especialista", "coordenador",
    "coordenadora", "gerente", "lider", "lead", "head", "diretor",
    "diretora", "supervisor", "supervisora", "arquiteto", "arquiteta",
    "principal", "staff", "iii", "ii", "manager", "professor", "professora",
]
TIPOS_ENTRADA = {
    "vacancy_type_internship", "vacancy_type_apprentice",
    "vacancy_type_trainee", "vacancy_type_lecturer",
}
SEM_EXP_DESC = re.compile(
    r"(sem experiencia|nao e necessario experiencia|nao exige experiencia|"
    r"experiencia nao e obrigatoria|primeiro emprego|nao precisa de experiencia)"
)
# Vagas de teste esquecidas na Gupy, modelos, etc.
LIXO = re.compile(r"(^\[?teste\b|\[teste\]|teste correcao|vaga modelo|"
                  r"nao se candidatar|vaga ficticia)")
BANCO = re.compile(r"(banco de talentos|banco de curriculos|banco de vagas|"
                   r"futuras oportunidades|central de talentos|talent pool)")
# Anúncios de "home office" que costumam ser venda por comissão / sem carteira
GOLPE = re.compile(r"(sem vinculo|comissao|comissoes|renda extra|"
                   r"ganhe ate|trabalhe de casa e ganhe|autonomo)")

TI_FORTE = [
    "desenvolvedor", "desenvolvedora", "developer", "dev", "programador",
    "programadora", "programacao", "python", "django", "java", "javascript",
    "php", "net", "react", "angular", "node", "android", "ios", "mobile",
    "software", "infraestrutura", "fullstack", "full stack", "front end",
    "frontend", "front", "backend", "back end", "devops", "cloud",
    "helpdesk", "help desk", "service desk", "linux", "redes", "qa",
    "banco de dados", "dba", "cientista de dados", "engenheiro de dados",
    "analista de dados", "data engineer", "data analyst", "data scientist",
    "machine learning", "inteligencia artificial", "ia", "rpa",
    "seguranca da informacao", "ciberseguranca", "cyber", "cybersecurity",
    "red team", "blue team", "soc", "pentest", "ux", "ui", "erp", "sap",
    "analista de sistemas", "analista de requisitos", "tecnologia da informacao",
    "ti", "web", "bi", "power bi", "suporte tecnico", "suporte de ti",
    "analista de suporte", "tecnico de informatica", "informatica",
]
TI_FRACO = ["suporte", "dados", "data", "sistemas", "teste", "testes",
            "tecnologia", "desenvolvimento", "automacao"]
ADM = [
    "administrativo", "administrativa", "atendimento", "atendente", "sac",
    "cliente", "recepcionista", "financeiro", "financeira", "rh",
    "recursos humanos", "departamento pessoal", "comercial", "vendas",
    "telemarketing", "backoffice", "back office", "faturamento", "contas",
    "secretaria", "escritorio", "cobranca", "call center", "televendas",
    "contabil", "contabilidade", "fiscal", "compras", "sdr", "pre vendas",
    "relacionamento", "caixa",
]


def normaliza(txt):
    txt = unicodedata.normalize("NFKD", txt or "").encode("ascii", "ignore")
    return re.sub(r"\s+", " ", txt.decode().lower()).strip()


def tem_palavra(texto, palavras):
    return any(re.search(rf"\b{re.escape(p)}\b", texto) for p in palavras)


def sem_tags(txt):
    return html.unescape(re.sub(r"<[^>]+>", " ", txt or "")).strip()


CIDADES_NORM = {normaliza(c) for c in CIDADES_PERTO}


def cidade_perto(local):
    n = normaliza(local)
    return any(re.search(rf"\b{re.escape(c)}\b", n) for c in CIDADES_NORM)


def baixar(url, tentativas=2, json_=False):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
    for i in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                corpo = r.read().decode("utf-8", "replace")
            return json.loads(corpo) if json_ else corpo
        except Exception as e:
            if i + 1 == tentativas:
                raise
            time.sleep(2 + 3 * i)


def vaga(fonte, titulo, link, empresa="", local="", modo="?",
         publicada="", descricao="", entrada_site=None):
    """Formato comum de vaga para todas as fontes.

    modo: "remoto", "híbrido", "presencial" ou "?".
    entrada_site: True/False se o próprio site diz o nível; None se não diz.
    """
    return {
        "fonte": fonte, "titulo": " ".join((titulo or "").split()),
        "link": link, "empresa": " ".join((empresa or "").split()),
        "local": " ".join((local or "").split()), "modo": modo,
        "publicada": publicada or "", "descricao": descricao or "",
        "entrada_site": entrada_site,
    }


# ---------------------------------------------------------------- Gupy

GUPY_API = "https://employability-portal.gupy.io/api/v1/jobs"
GUPY_MODO = {"hybrid": "híbrido", "on-site": "presencial", "remote": "remoto"}


def _gupy(**filtros):
    vagas, offset = [], 0
    while True:
        params = urllib.parse.urlencode({**filtros, "limit": 100, "offset": offset})
        dados = baixar(f"{GUPY_API}?{params}", json_=True).get("data", [])
        if not dados:
            break
        vagas.extend(dados)
        offset += 100
        time.sleep(0.3)
    return vagas


def _gupy_para_vaga(v):
    return vaga(
        "gupy", v["name"], v.get("jobUrl"), v.get("careerPageName"),
        f"{v.get('city') or ''} - {v.get('state') or ''}".strip(" -"),
        GUPY_MODO.get(v.get("workplaceType"), "?"),
        (v.get("publishedDate") or "")[:10], v.get("description"),
        True if v.get("type") in TIPOS_ENTRADA else None,
    )


def fonte_gupy():
    vagas = [_gupy_para_vaga(v) for v in _gupy(workplaceType="remote")]
    for cidade in CIDADES_PERTO:
        for v in _gupy(city=cidade):
            if normaliza(v.get("state")) in ("minas gerais", "mg", ""):
                vagas.append(_gupy_para_vaga(v))  # evita homônimas de outro estado
    return vagas


# ---------------------------------------------------------------- InfoJobs

IJ = "https://www.infojobs.com.br"


def _slug(cidade):
    return normaliza(cidade).replace(" ", "-")


def _infojobs_pagina(caminho, pagina):
    h = baixar(f"{IJ}/{caminho}" + (f"?Page={pagina}" if pagina > 1 else ""))
    vagas = []
    partes = h.split('class="pt-24 px-24 cursor-pointer js_vacancyLoad')[1:]
    for p in partes:
        href = re.search(r'data-href="([^"]+)"', p)
        titulo = re.search(r'js_vacancyTitle">\s*(.*?)\s*</h2>', p, re.S)
        if not (href and titulo):
            continue
        data = re.search(r'js_date" data-value="(\d{4})/(\d{2})/(\d{2})', p)
        empresa = re.search(r'<a class="text-body text-decoration-none"[^>]*>(.*?)</a>', p, re.S)
        local = re.search(r'<div class="mb-8">\s*([^<]+)', p)
        tags_bloco = re.search(r'flex-wrap mb-8 text-medium"(.*?)<div class="text-medium">', p, re.S)
        tags = normaliza(sem_tags(tags_bloco.group(1))) if tags_bloco else ""
        desc = re.search(r'<div class="text-medium">\s*(.*?)</div>', p, re.S)
        modo = ("remoto" if "home office" in tags else
                "híbrido" if "hibrido" in tags else
                "presencial" if "presencial" in tags else "?")
        vagas.append(vaga(
            "infojobs", sem_tags(titulo.group(1)), IJ + href.group(1),
            sem_tags(empresa.group(1)) if empresa else "",
            local.group(1).strip() if local else "", modo,
            "-".join(data.groups()) if data else "",
            sem_tags(desc.group(1)) if desc else "",
            True if "sem experiencia" in tags else None,
        ))
    return vagas


def _infojobs(caminho, max_paginas):
    vagas, vistos = [], set()
    for pagina in range(1, max_paginas + 1):
        novas = [v for v in _infojobs_pagina(caminho, pagina) if v["link"] not in vistos]
        if not novas:
            break
        vistos.update(v["link"] for v in novas)
        vagas.extend(novas)
        time.sleep(0.5)
    return vagas


def fonte_infojobs():
    vagas = []
    for cidade in CIDADES_PERTO:
        grandes = cidade in ("Montes Claros", "Janaúba")
        vagas += [v for v in _infojobs(f"vagas-de-emprego-em-{_slug(cidade)},-mg.aspx",
                                       10 if grandes else 2)
                  if cidade_perto(v["local"])]
    vagas += _infojobs("vagas-de-emprego-sem-experiencia-trabalho-home-office.aspx", 15)
    return vagas


# ---------------------------------------------------------------- LinkedIn
# A busca pública do LinkedIn ignora o filtro "remoto", então aqui só região.

LI = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"


def _linkedin(local, distancia_milhas, max_paginas=10):
    vagas = []
    for pagina in range(max_paginas):
        params = urllib.parse.urlencode({
            "keywords": "", "location": local, "distance": distancia_milhas,
            "f_TPR": "r2592000", "start": pagina * 10})
        h = baixar(f"{LI}?{params}")
        cards = h.split('<div class="base-card')[1:]
        if not cards:
            break
        for c in cards:
            link = re.search(r'base-card__full-link[^>]*href="([^"?]+)', c)
            titulo = re.search(r'base-search-card__title">\s*(.*?)\s*</h3>', c, re.S)
            if not (link and titulo):
                continue
            empresa = re.search(r'base-search-card__subtitle">(.*?)</h4>', c, re.S)
            loc = re.search(r'job-search-card__location">\s*(.*?)\s*</span>', c, re.S)
            data = re.search(r'datetime="([\d-]+)"', c)
            vagas.append(vaga(
                "linkedin", sem_tags(titulo.group(1)), link.group(1),
                sem_tags(empresa.group(1)) if empresa else "",
                sem_tags(loc.group(1)) if loc else "", "?",
                data.group(1) if data else ""))
        time.sleep(1.5)
    return [v for v in vagas if re.search(r"\b(minas gerais|mg)\b", normaliza(v["local"]))]


def fonte_linkedin():
    return (_linkedin("Januária, Minas Gerais, Brasil", 60)
            + _linkedin("Montes Claros, Minas Gerais, Brasil", 10))


# ---------------------------------------------------------------- Remotar

REMOTAR_BUSCAS = ["junior", "estagio", "trainee", "assistente", "auxiliar",
                  "atendente", "aprendiz", "sem experiencia", "suporte"]


def fonte_remotar(desde):
    vagas = []
    for termo in REMOTAR_BUSCAS:
        for pagina in range(1, 30):
            q = urllib.parse.urlencode({"page": pagina, "search": termo})
            dados = baixar(f"https://api.remotar.com.br/jobs?{q}", json_=True).get("data", [])
            if not dados:
                break
            for j in dados:
                if j.get("type") != "remote" or j.get("expired") or not j.get("active", True):
                    continue
                vagas.append(vaga(
                    "remotar", j.get("title"),
                    j.get("externalLink") or f"https://remotar.com.br/job/{j['id']}",
                    (j.get("company") or {}).get("name"), "", "remoto",
                    (j.get("createdAt") or "")[:10], sem_tags(j.get("description"))))
            if (dados[-1].get("createdAt") or "9999")[:10] < desde:
                break  # os resultados vêm dos mais novos para os mais velhos
            time.sleep(0.3)
    return vagas


# ---------------------------------------------------------------- GitHub (TI)

GITHUB_REPOS = ["backend-br/vagas", "frontendbr/vagas", "qa-brasil/vagas",
                "react-brasil/vagas", "androiddevbr/vagas"]


def fonte_github():
    vagas = []
    for repo in GITHUB_REPOS:
        issues = baixar(f"https://api.github.com/repos/{repo}/issues"
                        "?state=open&per_page=100", json_=True)
        for i in issues:
            if "pull_request" in i:
                continue
            labels = normaliza(" ".join(l["name"] for l in i["labels"]))
            texto = normaliza(i["title"] + " " + labels)
            modo = ("remoto" if "remoto" in texto else
                    "híbrido" if "hibrido" in texto else
                    "presencial" if "presencial" in texto else "?")
            nivel = (False if tem_palavra(labels, ["senior", "pleno", "especialista"]) else
                     True if tem_palavra(labels, ["junior", "estagio", "trainee"]) else None)
            vagas.append(vaga("github", i["title"], i["html_url"], "", repo, modo,
                              i["created_at"][:10], i.get("body") or "", nivel))
    return vagas


# ---------------------------------------------------------------- vagas.com.br

VG = "https://www.vagas.com.br"
VG_ENTRADA = ("auxiliar", "operacional", "estagiario", "trainee", "junior")
VG_EXP = ("pleno", "senior", "gerencia", "supervisao", "diretoria", "especialista")


def _vagascombr(caminho, modo, max_paginas):
    vagas = []
    for pagina in range(1, max_paginas + 1):
        h = baixar(f"{VG}/{caminho}?ordenar_por=mais_recentes&pagina={pagina}")
        cards = h.split('<li class="vaga ')[1:]
        if not cards:
            break
        for c in cards:
            a = re.search(r'link-detalhes-vaga"[^>]*title="([^"]+)"[^>]*href="([^"]+)"', c)
            if not a:
                continue
            empresa = re.search(r'class="emprVaga">(.*?)</span>', c, re.S)
            nivel = normaliza(sem_tags((re.search(r'class="nivelVaga">(.*?)</span>', c, re.S)
                                        or [None, ""])[1]))
            local = re.search(r'class="vaga-local">(.*?)<div', c, re.S)
            data = re.search(r'data-publicacao">.*?(\d{2})/(\d{2})/(\d{4})', c, re.S)
            desc = re.search(r'<div class="detalhes">\s*<p>(.*?)</p>', c, re.S)
            entrada = (False if any(p in nivel for p in VG_EXP) else
                       True if any(p in nivel for p in VG_ENTRADA) else None)
            vagas.append(vaga(
                "vagas.com.br", html.unescape(a.group(1)), VG + a.group(2),
                sem_tags(empresa.group(1)) if empresa else "",
                sem_tags(local.group(1)) if local else "", modo,
                "-".join(reversed(data.groups())) if data else "",
                sem_tags(desc.group(1)) if desc else "", entrada))
        time.sleep(0.5)
    return vagas


def fonte_vagascombr():
    vagas = []
    for cidade in ("Montes Claros", "Januária", "Janaúba"):
        vagas += [v for v in _vagascombr(f"vagas-em-{_slug(cidade)}-mg", "?", 3)
                  if cidade_perto(v["local"])]
    # A página "home office" do site não é confiável (mistura híbrido e presencial)
    return vagas


# ---------------------------------------------------------------- filtros

def classificar_area(nome):
    n = normaliza(nome)
    if tem_palavra(n, TI_FORTE):
        return "ti"
    if tem_palavra(n, ADM):
        return "adm"
    if tem_palavra(n, TI_FRACO):
        return "ti"
    return "outros"


def e_entrada(v):
    nome = normaliza(v["titulo"])
    if v["entrada_site"] is False or tem_palavra(nome, EXCLUIR):
        return False
    if v["entrada_site"] or tem_palavra(nome, ENTRADA):
        return True
    return bool(SEM_EXP_DESC.search(normaliza(v["descricao"])))


def chave(v):
    """Identifica a mesma vaga repetida (ou vinda de sites diferentes)."""
    t = re.sub(r"[^a-z0-9]", "", normaliza(v["titulo"]))
    e = re.sub(r"[^a-z0-9]", "", normaliza(v["empresa"]))
    return f"{t}|{e}"


FONTES = {
    "gupy": fonte_gupy, "infojobs": fonte_infojobs, "linkedin": fonte_linkedin,
    "remotar": fonte_remotar, "github": fonte_github, "vagas.com.br": fonte_vagascombr,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--area", choices=["ti", "adm", "outros"])
    ap.add_argument("--novas", action="store_true",
                    help="mostra só vagas que não apareceram em buscas anteriores")
    ap.add_argument("--dias", type=int, default=60,
                    help="ignora vagas publicadas há mais de N dias (padrão 60)")
    ap.add_argument("--fontes", default=",".join(FONTES),
                    help=f"lista separada por vírgula: {','.join(FONTES)}")
    ap.add_argument("--banco", action="store_true",
                    help='inclui vagas de "Banco de Talentos"')
    args = ap.parse_args()

    fontes = [f.strip() for f in args.fontes.split(",") if f.strip()]
    for f in fontes:
        if f not in FONTES:
            ap.error(f"fonte desconhecida: {f}")
    desde = (date.today() - timedelta(days=args.dias)).isoformat()

    print(f"Buscando em: {', '.join(fontes)} (pode levar alguns minutos)...")
    todas = []
    with ThreadPoolExecutor(len(fontes)) as ex:
        futuros = {f: ex.submit(FONTES[f], desde) if f == "remotar"
                   else ex.submit(FONTES[f]) for f in fontes}
        for f, fut in futuros.items():
            try:
                res = fut.result()
                print(f"  {f:13} {len(res):5} vagas brutas")
                todas += res
            except Exception as e:
                print(f"  ! {f}: falhou ({e})")

    encontradas = {}  # chave -> linha
    for v in todas:
        nome = normaliza(v["titulo"])
        if not v["link"] or LIXO.search(nome) or GOLPE.search(nome):
            continue
        if not args.banco and BANCO.search(nome):
            continue
        if v["publicada"] and v["publicada"] < desde:
            continue
        perto = cidade_perto(v["local"]) or (v["fonte"] == "linkedin")
        if v["modo"] == "remoto":
            if not e_entrada(v):
                continue
            modo = "remoto"
        elif perto:
            # Perto de casa vale qualquer vaga que não peça experiência de nível pleno/sênior
            if v["entrada_site"] is False or tem_palavra(nome, EXCLUIR):
                continue
            cidade = v["local"].split(",")[0].split(" - ")[0].split(" / ")[0].strip()
            modo = f"{v['modo'] if v['modo'] != '?' else 'região'} - {cidade}"
        else:
            continue

        k = chave(v)
        if k in encontradas:
            linha = encontradas[k]
            linha["repeticoes"] += 1
            if v["fonte"] not in linha["fonte"]:
                linha["fonte"] += f",{v['fonte']}"
            continue
        sem_exp = bool(v["entrada_site"] and v["fonte"] == "infojobs"
                       or SEM_EXP_DESC.search(normaliza(v["descricao"]))
                       or "sem experiencia" in nome)
        encontradas[k] = {
            "nova": "", "area": classificar_area(v["titulo"]), "modo": modo,
            "vaga": v["titulo"], "empresa": v["empresa"],
            "sem_exp": "sim" if sem_exp else "", "publicada": v["publicada"],
            "fonte": v["fonte"], "repeticoes": 1, "link": v["link"], "_chave": k,
        }

    vistas = set(json.loads(VISTAS.read_text())) if VISTAS.exists() else set()

    linhas = []
    for linha in encontradas.values():
        if args.area and linha["area"] != args.area:
            continue
        nova = linha["_chave"] not in vistas
        if args.novas and not nova:
            continue
        linha["nova"] = "NOVA" if nova else ""
        linhas.append(linha)

    # Prioriza: perto de casa > "sem experiência" explícito > mais recente
    linhas.sort(key=lambda l: l["publicada"], reverse=True)
    linhas.sort(key=lambda l: (l["modo"] == "remoto", not l["sem_exp"]))

    saida = PASTA / f"vagas_{datetime.now():%Y-%m-%d_%H%M}.csv"
    campos = ["nova", "area", "modo", "vaga", "empresa", "sem_exp",
              "publicada", "fonte", "repeticoes", "link"]
    with open(saida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)

    for l in linhas:
        marca = "★" if l["sem_exp"] else " "
        rep = f" (x{l['repeticoes']})" if l["repeticoes"] > 1 else ""
        print(f"{marca} {l['nova']:4} [{l['area']:6}] {l['modo'][:28]:28} "
              f"{(l['vaga'] + rep)[:60]:60} | {l['empresa'][:22]:22} | {l['fonte']}\n"
              f"        {l['link']}")

    # Só marca como vistas as vagas que foram de fato mostradas
    VISTAS.write_text(json.dumps(sorted(vistas | {l["_chave"] for l in linhas})))
    novas = sum(1 for l in linhas if l["nova"])
    remotas = sum(1 for l in linhas if l["modo"] == "remoto")
    print(f"\n{len(linhas)} vagas ({novas} novas; {len(linhas) - remotas} na região, "
          f"{remotas} remotas). ★ = diz que não precisa de experiência.")
    print(f"Planilha salva em: {saida}")


if __name__ == "__main__":
    main()
