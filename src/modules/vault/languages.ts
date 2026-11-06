import { cpp } from "@codemirror/lang-cpp";
import { css } from "@codemirror/lang-css";
import { go } from "@codemirror/lang-go";
import { html } from "@codemirror/lang-html";
import { java } from "@codemirror/lang-java";
import { javascript } from "@codemirror/lang-javascript";
import { json } from "@codemirror/lang-json";
import { markdown } from "@codemirror/lang-markdown";
import { php } from "@codemirror/lang-php";
import { python } from "@codemirror/lang-python";
import { rust } from "@codemirror/lang-rust";
import { sql } from "@codemirror/lang-sql";
import { xml } from "@codemirror/lang-xml";
import { yaml } from "@codemirror/lang-yaml";
import type { Extension } from "@codemirror/state";

export const LANGS: { id: string; label: string; ext: () => Extension | null }[] = [
  { id: "javascript", label: "JavaScript", ext: () => javascript({ jsx: true }) },
  { id: "typescript", label: "TypeScript", ext: () => javascript({ typescript: true, jsx: true }) },
  { id: "python", label: "Python", ext: () => python() },
  { id: "rust", label: "Rust", ext: () => rust() },
  { id: "go", label: "Go", ext: () => go() },
  { id: "java", label: "Java", ext: () => java() },
  { id: "cpp", label: "C++", ext: () => cpp() },
  { id: "c", label: "C", ext: () => cpp() },
  { id: "php", label: "PHP", ext: () => php() },
  { id: "html", label: "HTML", ext: () => html() },
  { id: "css", label: "CSS", ext: () => css() },
  { id: "json", label: "JSON", ext: () => json() },
  { id: "yaml", label: "YAML", ext: () => yaml() },
  { id: "xml", label: "XML", ext: () => xml() },
  { id: "sql", label: "SQL", ext: () => sql() },
  { id: "markdown", label: "Markdown", ext: () => markdown() },
  { id: "shell", label: "Shell", ext: () => null },
  { id: "text", label: "Văn bản", ext: () => null },
];

export const langLabel = (id: string) => LANGS.find((l) => l.id === id)?.label ?? id;
export const langExt = (id: string): Extension[] => {
  const e = LANGS.find((l) => l.id === id)?.ext();
  return e ? [e] : [];
};
