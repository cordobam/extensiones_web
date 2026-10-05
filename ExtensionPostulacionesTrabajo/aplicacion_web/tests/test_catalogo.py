"""Catálogo de skills: normalización, índice de alias y detección en texto.

Los casos que se testean acá son los que rompen el matching más adelante. Un
alias mal escrito no tira un error visible: devuelve un puntaje raro y nadie
sabe por qué. Por eso los falsos positivos tienen su propio bloque de tests.
"""

from pathlib import Path

import pytest
import yaml

from radar.catalogo import (
    Catalogo,
    ErrorCatalogo,
    Skill,
    _cargar_archivo,
    catalogo,
    normalizar,
)

RUTA_REAL = Path(__file__).resolve().parent.parent / "catalogo" / "skills.yml"


@pytest.fixture(scope="module")
def cat() -> Catalogo:
    return catalogo()


def escribir_catalogo(tmp_path: Path, skills: list[dict], **extra) -> Path:
    ruta = tmp_path / "skills.yml"
    datos = {"version": 1, "skills": skills, **extra}
    ruta.write_text(yaml.safe_dump(datos, allow_unicode=True), encoding="utf-8")
    return ruta


# ------------------------------------------------------------------ normalizar


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("Power BI", "power bi"),
        ("power-bi", "power bi"),
        ("POWER_BI", "power bi"),
        ("power   bi", "power bi"),
        ("Node.js", "node.js"),
        ("Ingeniería de Datos", "ingenieria de datos"),
        ("Análisis", "analisis"),
        ("C++", "c++"),
        ("C#", "c#"),
        (".NET", ".net"),
        ("", ""),
        ("   ", ""),
        ("!!!", ""),
    ],
)
def test_normalizar(entrada: str, esperado: str) -> None:
    assert normalizar(entrada) == esperado


def test_normalizar_es_idempotente() -> None:
    una_vez = normalizar("Power-BI")
    assert normalizar(una_vez) == una_vez


# ------------------------------------------------------------------- resolver


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        # Nombre canónico, como lo escribió la persona.
        ("Python", "Python"),
        ("SQL", "SQL"),
        # Alias que aparecen así en las ofertas.
        ("pyspark", "PySpark"),
        ("spark", "Apache Spark"),
        ("Apache Spark", "Apache Spark"),
        ("apache  spark", "Apache Spark"),
        ("sklearn", "scikit-learn"),
        ("scikit-learn", "scikit-learn"),
        ("postgres", "PostgreSQL"),
        ("PostgreSQL", "PostgreSQL"),
        ("k8s", "Kubernetes"),
        ("pbi", "Power BI"),
        ("POWER BI", "Power BI"),
        ("node.js", "Node.js"),
        ("dbt core", "dbt"),
        ("big query", "BigQuery"),
        ("airflow", "Apache Airflow"),
        ("amazon web services", "AWS"),

        # Soporte IT.
        ("mesa de ayuda", "Mesa de Ayuda"),
        ("helpdesk", "Mesa de Ayuda"),
        ("mesa de ayuda N2", "Mesa de Ayuda"),
        ("active directory", "Active Directory"),
        ("azure ad", "Active Directory"),
        ("tcp/ip", "TCP/IP"),
        ("wifi", "Redes Inalámbricas"),
        ("o365", "Microsoft 365"),
        ("servicenow", "ServiceNow"),
        ("power shell", "PowerShell"),

        # Atención al cliente.
        ("servicio al cliente", "Atención al Cliente"),
        ("call center", "Contact Center"),
        ("zendesk", "Zendesk"),
        ("reclamos", "Manejo de Reclamos"),
        ("net promoter score", "NPS"),
        ("office 365", "Microsoft 365"),
    ],
)
def test_resolver_alias(cat: Catalogo, texto: str, esperado: str) -> None:
    assert cat.resolver(texto) == esperado


def test_resolver_ignora_mayusculas_y_acentos(cat: Catalogo) -> None:
    assert cat.resolver("INGENIERÍA DE DATOS") == "Analytics Engineering"
    assert cat.resolver("análisis de datos") == "Data Analysis"


def test_resolver_prefiere_la_forma_mas_larga(cat: Catalogo) -> None:
    """En "apache spark" importa más la skill completa que la palabra suelta."""
    assert cat.resolver("Apache Spark") == "Apache Spark"
    assert cat.resolver("mucha experiencia en spark y pyspark") == "PySpark"


@pytest.mark.parametrize("texto", ["", "   ", "habilidad blanda", "COBOL", "ZZZ"])
def test_resolver_desconocido_devuelve_none(cat: Catalogo, texto: str) -> None:
    assert cat.resolver(texto) is None


def test_resolver_de_una_letra_exacta(cat: Catalogo) -> None:
    """`R` no se indexa para no matchear todo, pero el nombre exacto sí resuelve."""
    assert cat.resolver("R") == "R"
    assert cat.resolver("r language") == "R"


# ------------------------------------------------------------------- detectar


def test_detectar_encuentra_todas_las_skills(cat: Catalogo) -> None:
    texto = "Buscamos Data Engineer con Python, Airflow y Snowflake."
    assert cat.detectar(texto) == ["Python", "Apache Airflow", "Snowflake"]


def test_detectar_devuelve_orden_de_aparicion(cat: Catalogo) -> None:
    texto = "Snowflake, Python y Docker."
    assert cat.detectar(texto) == ["Snowflake", "Python", "Docker"]


def test_detectar_no_repite_una_skill(cat: Catalogo) -> None:
    texto = "Python, Python, Python y más Python."
    assert cat.detectar(texto) == ["Python"]


def test_detectar_distingue_spark_de_pyspark(cat: Catalogo) -> None:
    """El caso que motiva los límites de palabra."""
    texto = "Necesitamos PySpark para trabajar con Apache Spark."
    assert cat.detectar(texto) == ["PySpark", "Apache Spark"]

    solo_spark = "Trabajo con Spark y nada más."
    assert cat.detectar(solo_spark) == ["Apache Spark"]


def test_detectar_en_oferta_de_soporte_it(cat: Catalogo) -> None:
    """Oferta de mesa de ayuda: los productos se nombran, así que matchea bien."""
    texto = (
        "Analista de Soporte Técnico (Mesa de Ayuda N1). "
        "Se requiere conocimiento en Windows 10, Active Directory, Office 365, "
        "TCP/IP y servicedesk con ServiceNow. Se valorará ITIL."
    )

    assert cat.detectar(texto) == [
        "Soporte Técnico",
        "Mesa de Ayuda",
        "Soporte Nivel 1",
        "Windows",
        "Active Directory",
        "Microsoft 365",
        "TCP/IP",
        "ServiceNow",
        "ITIL",
    ]


def test_detectar_en_oferta_de_atencion_cliente(cat: Catalogo) -> None:
    """Oferta de atención al cliente: mezcla skills con actividades.

    Fijate en lo que NO aparece: "30 llamados por hora" y "retención de clientes
    activos" no son skills detectables. Es la diferencia con soporte IT.
    """
    texto = (
        "Analista de Atención al Cliente para Contact Center. "
        "Manejo de reclamos y escalamiento. Essential: Zendesk y Salesforce. "
        "Se valorará experiencia en cobranzas y resolución de conflictos."
    )

    assert cat.detectar(texto) == [
        "Atención al Cliente",
        "Contact Center",
        "Manejo de Reclamos",
        "Escalamiento",
        "Zendesk",
        "Salesforce",
        "Cobranzas",
        "Resolución de Conflictos",
    ]


def test_detectar_sobre_oferta_realista(cat: Catalogo) -> None:
    titulo = "Data Engineer Senior (Python/PySpark/Snowflake)"
    descripcion = (
        "3+ anos de experiencia en Apache Spark y Python. "
        "Conocimientos: Airflow, dbt, Snowflake, Docker, Kubernetes, SQL avanzado. "
        "Extras: GitHub Actions, Power BI, BigQuery, machine learning con XGBoost. "
        "Se valoraria Node.js y Kafka."
    )

    assert cat.detectar(titulo) == ["Python", "PySpark", "Snowflake"]

    encontradas = set(cat.detectar(descripcion))
    esperadas = {
        "Apache Spark", "Python", "Apache Airflow", "dbt", "Snowflake",
        "Docker", "Kubernetes", "SQL", "GitHub Actions", "Power BI",
        "BigQuery", "Machine Learning", "XGBoost", "Node.js", "Apache Kafka",
    }
    assert encontradas == esperadas


# ------------------------------------------------- falsos positivos (lo importante)


@pytest.mark.parametrize(
    "texto",
    [
        # "spark" no puede aparecer dentro de una palabra.
        "Sparkline de ventas del primer trimestre",
        # "js" no puede aparecer dentro de "node.js".
        "Hecho en node.jsx y d3.js",
        # Una letra suelta matchearía todo el texto.
        "R$ 300.000 mensuales",
        # Estas palabras parecen skills pero no lo son.
        "Buscamos un go getter para el equipo",
        "Empresa lider del sector, Tcl iterativo",
        "Ofrecemos banio en la oficina y un comedor",
        # Palapas inglesas que se parecen a tools de soporte.
        "That was a nice firewall of jokes",
        "The switch is on, but the router is off",
        "Great exposition of the plot and the characters",
    ],
)
def test_detectar_no_inventa_skills(cat: Catalogo, texto: str) -> None:
    assert cat.detectar(texto) == []


def test_detectar_ignora_puntuacion_sin_perder_coincidencias(cat: Catalogo) -> None:
    """El punto de fin de oración no es parte de la palabra.

    Antes esto fallaba: "Kafka." no detectaba Kafka, porque el punto estaba en el
    conjunto de caracteres de palabra.
    """
    assert cat.detectar("Usamos Kafka. Spark, Power BI. ETL.") == [
        "Apache Kafka",
        "Apache Spark",
        "Power BI",
        "ETL",
    ]


def test_detectar_texto_vacio(cat: Catalogo) -> None:
    assert cat.detectar("") == []
    assert cat.detectar("    ") == []
    assert cat.detectar("--- /// ,,, ***") == []


# --------------------------------------------------------------- índice y API


def test_buscar_nombre_solo_acepta_canonicos(cat: Catalogo) -> None:
    assert cat.buscar_nombre("python").nombre == "Python"
    assert cat.buscar_nombre("  APACHE SPARK  ").nombre == "Apache Spark"
    # "spark" es un alias, no un nombre canónico: para eso está `resolver`.
    assert cat.buscar_nombre("spark") is None


def test_por_categoria_agrupa_y_ordena(cat: Catalogo) -> None:
    grupos = cat.por_categoria()
    assert "datos" in grupos
    assert "Apache Spark" in grupos["datos"]
    assert grupos["datos"] == sorted(grupos["datos"])
    # Las categorías del archivo, en orden alfabético.
    assert list(grupos) == sorted(grupos)


def test_sin_conocer_avisa_las_que_faltan(cat: Catalogo) -> None:
    resultado = cat.sin_conocer(["Python", "Airflow", "Cobol", "Habilidad blanda", "SAP"])
    assert resultado == ["Cobol", "Habilidad blanda", "SAP"]


def test_sin_conocer_acepta_alias_como_conocidas(cat: Catalogo) -> None:
    """"Airflow" es un alias, no un error: la skill existe y se llama Apache Airflow."""
    assert cat.sin_conocer(["Airflow", "sklearn", "k8s"]) == []


def test_normalizar_lista_sugiere_el_nombre_canonico(cat: Catalogo) -> None:
    cambios = cat.normalizar_lista(["sklearn", "k8s", "Airflow", "Python", "inventada"])
    assert cambios == {
        "sklearn": "scikit-learn",
        "k8s": "Kubernetes",
        "Airflow": "Apache Airflow",
    }


def test_sin_conocer_ignora_vacios(cat: Catalogo) -> None:
    assert cat.sin_conocer(["", "   ", "Python"]) == []


# --------------------------------------------------------- integridad del YAML


def test_catalogo_real_carga(tmp_path: Path) -> None:
    """El archivo que se versiona tiene que ser válido y consistente."""
    cat = _cargar_archivo(RUTA_REAL)
    assert len(cat) > 200
    assert len(cat.por_categoria()) == 12

    # Cada skill tiene que ser resoluble por su propio nombre canónico.
    for skill in cat.skills:
        assert cat.resolver(skill.nombre) == skill.nombre, skill.nombre


def test_las_categorias_de_soporte_y_atencion_existen() -> None:
    cat = catalogo()
    grupos = cat.por_categoria()
    assert len(grupos["soporte_it"]) > 40
    assert len(grupos["atencion_cliente"]) > 20

    for esperada in ("Mesa de Ayuda", "Active Directory", "TCP/IP", "ServiceNow"):
        assert esperada in grupos["soporte_it"], esperada
    for esperada in ("Atención al Cliente", "Zendesk", "Salesforce", "SLA"):
        assert esperada in grupos["atencion_cliente"], esperada


def test_no_duplica_skills_que_ya_tenia_otra_categoria() -> None:
    """Linux, Excel, SQL y los demás viven en su categoría original.

    `categoria` es un solo string, así que una skill tiene un solo hogar. Estas
    no se copiaron a soporte_it: `detectar` las encuentra igual, la categoría
    sólo sirve para agrupar y filtrar.
    """
    cat = catalogo()
    grupos = cat.por_categoria()

    assert "Linux" in grupos["infraestructura"]
    assert "Linux" not in grupos["soporte_it"]
    assert "Excel" in grupos["bi"]
    assert "Excel" not in grupos["atencion_cliente"]
    assert "SQL" in grupos["lenguaje"]

    # Y siguen detectándose, que es lo que importa para el matching.
    assert "Linux" in cat.detectar("Linux y Windows en el servidor")
    assert "Bash" in cat.detectar("Scripts en Bash")


def test_alias_repetido_entre_skills_falla(tmp_path: Path) -> None:
    ruta = escribir_catalogo(
        tmp_path,
        [
            {"nombre": "Apache Spark", "categoria": "datos", "alias": ["spark"]},
            {"nombre": "PySpark", "categoria": "datos", "alias": ["spark"]},
        ],
    )
    with pytest.raises(ErrorCatalogo, match="dos skills"):
        _cargar_archivo(ruta)


def test_alias_igual_al_propio_nombre_no_es_colision(tmp_path: Path) -> None:
    """`ETL` con alias `etl` es la misma skill, no un choque."""
    ruta = escribir_catalogo(
        tmp_path,
        [{"nombre": "ETL", "categoria": "analisis", "alias": ["etl", "elt"]}],
    )
    cat = _cargar_archivo(ruta)
    assert cat.resolver("etl") == "ETL"
    assert cat.resolver("elt") == "ETL"


def test_nombre_canonico_repetido_falla(tmp_path: Path) -> None:
    ruta = escribir_catalogo(
        tmp_path,
        [
            {"nombre": "Python", "categoria": "lenguaje"},
            {"nombre": "python", "categoria": "lenguaje"},
        ],
    )
    with pytest.raises(ErrorCatalogo, match="repetida"):
        _cargar_archivo(ruta)


def test_alias_repetido_dentro_de_una_skill_falla(tmp_path: Path) -> None:
    ruta = escribir_catalogo(
        tmp_path,
        [{"nombre": "Python", "categoria": "lenguaje", "alias": ["py", "PY"]}],
    )
    with pytest.raises(ValueError, match="Alias repetido"):
        _cargar_archivo(ruta)


def test_categoria_sin_declarar_falla(tmp_path: Path) -> None:
    ruta = escribir_catalogo(
        tmp_path,
        [{"nombre": "Python", "categoria": "lenguaje"}],
        categorias=["datos", "web"],
    )
    with pytest.raises(ErrorCatalogo, match="sin declarar"):
        _cargar_archivo(ruta)


def test_catalogo_vacio_falla(tmp_path: Path) -> None:
    ruta = escribir_catalogo(tmp_path, [])
    with pytest.raises(ErrorCatalogo, match="no tiene ninguna skill"):
        _cargar_archivo(ruta)


def test_archivo_inexistente_falla(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _cargar_archivo(tmp_path / "no-existe.yml")


def test_campo_desconocido_falla(tmp_path: Path) -> None:
    """Un typo en el YAML tiene que fallar, no ignorarse en silencio."""
    ruta = escribir_catalogo(
        tmp_path,
        [{"nombre": "Python", "categoria": "lenguaje", "nivel": " senior "}],
    )
    with pytest.raises(ValueError):
        _cargar_archivo(ruta)


def test_alias_de_una_sola_letra_no_se_indexa(tmp_path: Path) -> None:
    """Documenta la limitación: "R" no se busca, pero el nombre exacto sí."""
    ruta = escribir_catalogo(
        tmp_path,
        [{"nombre": "Q", "categoria": "datos", "alias": ["q", "qlike"]}],
    )
    cat = _cargar_archivo(ruta)
    assert cat.resolver("Q") == "Q"
    assert cat.resolver("qlike") == "Q"
    # Buscar "q" en un texto largo no matchearía de forma útil.
    assert cat.detectar("quelco con q posta") == []