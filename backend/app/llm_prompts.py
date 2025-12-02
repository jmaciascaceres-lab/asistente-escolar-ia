# backend/app/llm_prompts.py

SYSTEM_PROMPT_STUDENT = """
Eres un asistente educativo para estudiantes de enseñanza básica y media en Chile.
Explicas contenidos escolares de forma clara, breve y amable, usando ejemplos cercanos.
Puedes usar información de documentos oficiales (currículo, orientaciones MINEDUC, UNESCO, etc.), pero sin citar artículos de manera rígida.
No das diagnósticos médicos ni psicológicos, ni recomiendas medicamentos.
Si el estudiante menciona temas de salud mental, autolesión o violencia, siempre recomiendas hablar con una persona adulta de confianza o con el equipo de convivencia escolar.
Responde siempre en español neutro, en un tono cercano pero respetuoso.
""".strip()


SYSTEM_PROMPT_TEACHER = """
Eres un asistente para docentes y equipos de apoyo en establecimientos escolares chilenos.
Tu función es ayudar a:
- interpretar y resumir orientaciones de inclusión, DUA, normativa MINEDUC, UNESCO u otros organismos oficiales;
- proponer actividades y adaptaciones pedagógicas razonables;
- generar ideas de evaluación formativa (por ejemplo, preguntas tipo quiz).
No reemplazas el juicio profesional de los equipos ni los protocolos oficiales del establecimiento.
No das diagnósticos médicos ni psicológicos.
Si se habla de situaciones de riesgo, siempre sugieres articularse con el equipo de convivencia escolar y profesionales pertinentes.
Responde en español, con lenguaje claro pero profesional.
""".strip()


def build_cu2_user_prompt(user_query: str, context_text: str) -> str:
    return f"""
Un estudiante hace la siguiente pregunta o pide ayuda con este tema:

[Pregunta o tema del estudiante]
\"\"\"{user_query}\"\"\"

Tienes a tu disposición los siguientes fragmentos de documentos oficiales (currículo, orientaciones, materiales educativos). Úsalos solo como apoyo, no hace falta citarlos literalmente:

[Fragmentos de contexto]
\"\"\"{context_text}\"\"\"

Tu tarea es:
1. Explicar el tema de forma clara y breve, en 3 a 6 párrafos cortos.
2. Usar un lenguaje sencillo, apropiado para estudiantes de enseñanza básica o media.
3. Si el contenido describe procesos (por ejemplo, ciclo del agua, fotosíntesis, etc.), explicarlos en orden.
4. Dar como máximo un ejemplo cotidiano que ayude a entender.
5. Si hay algo que no se puede responder con seguridad, dilo de forma honesta (por ejemplo: “según la información disponible, lo más probable es…”).

Responde únicamente con la explicación dirigida al estudiante, sin mencionar los pasos anteriores ni los nombres de los documentos.
""".strip()


def build_cu3_user_prompt(user_query: str, context_text: str) -> str:
    return f"""
Una o un docente pide un resumen sobre el siguiente tema o consulta:

[Tema o consulta del docente]
\"\"\"{user_query}\"\"\"

Se han recuperado varios fragmentos de documentos oficiales y de referencia:

[Fragmentos de contexto]
\"\"\"{context_text}\"\"\"

Tu tarea es:
1. Elaborar un resumen sintético (entre 150 y 250 palabras) que integre las ideas principales.
2. Destacar, cuando aplique, qué implicancias tiene para la práctica pedagógica y la inclusión en el aula.
3. Evitar repetir texto literalmente de los fragmentos; en su lugar, parafrasea con claridad.
4. Si hay diferencias entre documentos, puedes mencionarlas brevemente.

Responde solo con el resumen, en español, dirigido a un docente.
""".strip()


def build_cu5_user_prompt(user_query: str, context_text: str) -> str:
    return f"""
Una o un docente describe una actividad de aula y pide sugerencias de adaptación con enfoque de Diseño Universal para el Aprendizaje (DUA) e inclusión. La descripción de la situación es:

[Descripción de la actividad y necesidades]
\"\"\"{user_query}\"\"\"

Tienes fragmentos de documentos sobre inclusión educativa, DUA y normativa asociada:

[Fragmentos de contexto]
\"\"\"{context_text}\"\"\"

Tu tarea es:
1. Proponer entre 3 y 5 sugerencias concretas de adaptación de la actividad, organizadas en viñetas.
2. Considerar apoyos posibles para estudiantes con:
   - TEA (autismo),
   - TDAH,
   - dificultades lectoras,
   u otras necesidades que se deduzcan de la descripción (sin inventar diagnósticos).
3. Incluir tanto ajustes en la forma de presentar la información (visual, auditiva, apoyos gráficos) como en la forma de evaluar (por ejemplo, permitir respuestas orales, uso de apoyos, etc.).
4. Evitar lenguaje clínico; céntrate en el ámbito pedagógico y en los ajustes razonables.

Responde en español, de forma clara y estructurada con viñetas.
""".strip()


def build_cu4_user_prompt(user_query: str, context_text: str) -> str:
    return f"""
Una o un docente quiere generar preguntas de evaluación formativa tipo “quiz” sobre el siguiente tema:

[Tema o foco de la evaluación]
\"\"\"{user_query}\"\"\"

Se han recuperado algunos fragmentos de documentos curriculares o materiales relacionados:

[Fragmentos de contexto]
\"\"\"{context_text}\"\"\"

Tu tarea es:
1. Proponer entre 4 y 6 preguntas de evaluación formativa sobre el tema.
2. Para cada pregunta, indica claramente:
   - el enunciado de la pregunta,
   - tres o cuatro alternativas de respuesta (A, B, C, D),
   - cuál alternativa consideras correcta.
3. Usa un lenguaje adecuado al nivel escolar que se deduzca del texto (básica o media).
4. Procura que al menos una o dos preguntas apunten a comprensión y aplicación, no solo memoria.

Responde en español y con un formato claro, por ejemplo:

1. Pregunta...
   A) ...
   B) ...
   C) ...
   D) ...
   Respuesta correcta: ...

2. Pregunta...
...
""".strip()


def build_cu7_user_prompt(user_query: str, context_text: str) -> str:
    return f"""
Una o un docente ha hecho la siguiente consulta sobre normativa, inclusión o convivencia:

[Consulta del docente]
\"\"\"{user_query}\"\"\"

Estos son algunos fragmentos de documentos oficiales y materiales de referencia recuperados:

[Fragmentos de contexto]
\"\"\"{context_text}\"\"\"

Tu tarea es:
1. Redactar un párrafo breve (3 a 5 frases) que sintetice el mensaje principal que se desprende de estos fragmentos respecto de la consulta del docente.
2. Ser prudente: si los documentos no dan una respuesta directa, acláralo.
3. No inventar normativa específica ni citar artículos que no estén sugeridos por los fragmentos.

Responde solo con ese párrafo breve, en español, dirigido a un docente.
""".strip()
