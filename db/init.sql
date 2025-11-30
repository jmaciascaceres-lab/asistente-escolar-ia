-- Extensiones útiles (opcional, pero recomendables)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- Tipo enum para roles de usuario
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role') THEN
    CREATE TYPE user_role AS ENUM ('student', 'teacher', 'caregiver', 'coordinator');
  END IF;
END
$$;

-- Tabla de cursos (8B, I Medio, etc.)
CREATE TABLE IF NOT EXISTS courses (
  id          BIGSERIAL PRIMARY KEY,
  code        TEXT NOT NULL,           -- ej: '8B'
  name        TEXT,                    -- ej: 'Octavo Básico B'
  level       TEXT,                    -- ej: '8_basico', '1_medio'
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

-- Tabla de usuarios
CREATE TABLE IF NOT EXISTS users (
  id          BIGSERIAL PRIMARY KEY,
  telegram_id BIGINT UNIQUE NOT NULL,
  role        user_role NOT NULL,
  course_id   BIGINT REFERENCES courses(id) ON DELETE SET NULL,
  settings    JSONB DEFAULT '{}'::jsonb,  -- modo baja estimulación, etc.
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

-- Relación apoderado-estudiante (muchos a muchos por si acaso)
CREATE TABLE IF NOT EXISTS caregiver_students (
  id            BIGSERIAL PRIMARY KEY,
  caregiver_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  student_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (caregiver_id, student_id)
);

-- Actualización de profesor jefe (después de users para tener FK)
ALTER TABLE courses
  ADD COLUMN IF NOT EXISTS homeroom_teacher_id BIGINT REFERENCES users(id) ON DELETE SET NULL;

-- Tabla de documentos para RAG
CREATE TABLE IF NOT EXISTS documents (
  id          BIGSERIAL PRIMARY KEY,
  title       TEXT NOT NULL,
  doc_type    TEXT NOT NULL,      -- 'normativa_internacional', 'inclusion_autismo', 'curriculo', etc.
  source      TEXT,               -- 'UNESCO', 'MINEDUC', 'Colegio X', etc.
  subject     TEXT,               -- 'Lenguaje', 'Historia', etc.
  grade_min   INT,                -- rango de curso (opcional)
  grade_max   INT,
  year        INT,
  url         TEXT,               -- ruta/URL al PDF original
  metadata    JSONB DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

-- Tabla de chunks para vectores (RAG)
CREATE TABLE IF NOT EXISTS document_chunks (
  id          BIGSERIAL PRIMARY KEY,
  document_id BIGINT REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  content     TEXT NOT NULL,
  embedding   vector(384),
  metadata    JSONB DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id
  ON document_chunks (document_id);

-- Tabla de interacciones para telemetría
CREATE TABLE IF NOT EXISTS interaction_logs (
  id              BIGSERIAL PRIMARY KEY,
  timestamp       TIMESTAMPTZ DEFAULT now(),
  user_id         BIGINT REFERENCES users(id) ON DELETE SET NULL,
  role            user_role NOT NULL,
  course_id       BIGINT REFERENCES courses(id) ON DELETE SET NULL,
  command         TEXT NOT NULL,        -- '/tarea', '/fuente', etc.
  case_id         TEXT,                 -- 'CU1'..'CU8'
  latency_ms      INT,
  used_rag        BOOLEAN DEFAULT FALSE,
  used_cag        BOOLEAN DEFAULT FALSE,
  sensitive_flag  BOOLEAN DEFAULT FALSE,
  raw_query       TEXT,
  raw_reply       TEXT
);

-- Tabla de recordatorios (para /recordatorio y reportes)
CREATE TABLE IF NOT EXISTS reminders (
  id            BIGSERIAL PRIMARY KEY,
  user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  text          TEXT NOT NULL,
  due_at        TIMESTAMPTZ NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT now(),
  completed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_reminders_due_at
  ON reminders (due_at);

-- Tabla de alertas para teacher-in-the-loop
CREATE TABLE IF NOT EXISTS teacher_alerts (
  id            BIGSERIAL PRIMARY KEY,
  created_at    TIMESTAMPTZ DEFAULT now(),
  student_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
  course_id     BIGINT REFERENCES courses(id) ON DELETE SET NULL,
  alert_type    TEXT NOT NULL,       -- 'riesgo', 'desregulacion_emocional', 'conflicto_convivencia', etc.
  summary       TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending',  -- 'pending', 'in_review', 'resolved'
  last_update   TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_teacher_alerts_status
  ON teacher_alerts (status);

-- Índices útiles para consultas frecuentes
CREATE INDEX IF NOT EXISTS idx_users_role
  ON users (role);

CREATE INDEX IF NOT EXISTS idx_interaction_logs_user
  ON interaction_logs (user_id);

CREATE INDEX IF NOT EXISTS idx_interaction_logs_course
  ON interaction_logs (course_id);
