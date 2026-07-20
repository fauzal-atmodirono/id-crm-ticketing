import DOMPurify from 'dompurify';
import { marked } from 'marked';

// GitHub-flavoured markdown with soft line breaks (the agent emits single
// newlines between sentences and expects them preserved).
marked.setOptions({ breaks: true, gfm: true });

// Force every rendered link to open in a new tab without leaking the opener.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

/**
 * Render assistant/agent message text (markdown) to sanitized HTML.
 *
 * The text originates from the LLM, so it is treated as untrusted: marked
 * produces HTML and DOMPurify strips anything unsafe before it reaches v-html.
 */
export function renderMarkdown(text: string): string {
  const raw = marked.parse(text, { async: false }) as string;
  return DOMPurify.sanitize(raw);
}
