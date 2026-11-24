from pathlib import Path

from .config import Config, origin
from .errors import AppError
from .storage import private_json


def capture_structure(page, config: Config, output: Path) -> dict:
    if origin(page.url) != config.portal_origin:
        raise AppError("Диагностика доступна только на странице платформы, после завершения входа.")
    labels = ["МОЁ ОБУЧЕНИЕ", "ПЕРЕЙТИ К КУРСУ", "Начать попытку", "Проверить код",
              "Решение принято", config.course_title, config.module_title]
    # Не читаем value, HTML, href, URL, cookies, storage, имя профиля или произвольный текст.
    structure = page.evaluate("""labels => {
      const path = el => {
        const parts = [];
        for (let node = el; node && node.tagName; node = node.parentElement) {
          const tag = node.tagName.toLowerCase();
          let part = tag;
          if (node.parentElement) {
            const siblings = [...node.parentElement.children].filter(n => n.tagName === node.tagName);
            part += ':nth-of-type(' + (siblings.indexOf(node) + 1) + ')';
          }
          parts.unshift(part);
        }
        return parts.join(' > ');
      };
      const candidates = [...document.querySelectorAll('a,button,input,textarea,[contenteditable],iframe,h1,h2,h3,[role],summary')];
      const knownLabels = [...document.querySelectorAll('body *')].filter(el =>
        labels.includes((el.textContent || '').trim()) &&
        ![...el.children].some(c => labels.includes((c.textContent || '').trim())));
      const all = [...new Set([...candidates, ...knownLabels])].filter(el =>
        !(el.tagName === 'INPUT' && ['password','hidden'].includes(el.type)));
      return {
        truncated: all.length > 500,
        elements: all.slice(0, 500).map(el => ({
          tag: el.tagName.toLowerCase(),
          selector: path(el),
          visible: !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'),
          editable: el.isContentEditable,
          known_label_index: labels.indexOf((el.textContent || '').trim())
        })),
        editor_hints: {
          monaco: document.querySelectorAll('.monaco-editor').length,
          codemirror5: document.querySelectorAll('.CodeMirror').length,
          codemirror6: document.querySelectorAll('.cm-editor').length,
          ace: document.querySelectorAll('.ace_editor').length,
          textareas: document.querySelectorAll('textarea').length,
          frames: document.querySelectorAll('iframe').length
        }
      };
    }""", labels)
    for element in structure["elements"]:
        index = element.pop("known_label_index")
        if index >= 0:
            element["known_label"] = labels[index]
    structure["note"] = "Только структура основного документа. Селекторы nth-of-type временные: проверьте их вручную."
    private_json(output, structure)
    return structure


def capture_editor_structure(page, config: Config, output: Path, submission_trace: dict | None = None) -> dict:
    """Сохраняет только геометрию и типы полей, без текста задачи и кода."""
    if origin(page.url) != config.portal_origin:
        raise AppError("Диагностика редактора доступна только на странице платформы.")
    structure = page.evaluate("""() => {
      const queryDeep = (root, selector) => {
        const found = [...root.querySelectorAll(selector)];
        for (const el of root.querySelectorAll('*')) {
          if (el.shadowRoot) found.push(...queryDeep(el.shadowRoot, selector));
        }
        return found;
      };
      const editors = queryDeep(document, '.monaco-editor');
      const rect = el => {
        const r = el.getBoundingClientRect();
        return {x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height)};
      };
      const describe = el => el ? {
        tag: el.tagName.toLowerCase(),
        classes: typeof el.className === 'string' ? el.className.slice(0, 120) : '',
        role: el.getAttribute('role'),
        type: el.tagName === 'INPUT' ? el.type : null,
        visible: !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'),
        edit_context: !!el.editContext,
        content_editable: el.isContentEditable,
        editor_index: editors.findIndex(root => root.contains(el)),
        rect: rect(el)
      } : null;
      const fields = queryDeep(document, 'textarea, [role="textbox"], [contenteditable="true"]')
        .filter(el => el.tagName !== 'INPUT' || el.type !== 'password');
      const buttons = queryDeep(document, 'button, [role="button"]')
        .filter(el => /проверить\\s+код/i.test(el.textContent || ''));
      const panels = queryDeep(document, '[role="tabpanel"], [id$="-content-output"]');
      const resultLabels = ['Решение принято', 'Решение отклонено'];
      const visibleResults = resultLabels.filter(label => queryDeep(document, '*').some(el =>
        el.children.length === 0 && (el.textContent || '').trim() === label &&
        el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'));
      let active = document.activeElement;
      while (active?.shadowRoot?.activeElement) active = active.shadowRoot.activeElement;
      return {
        editor_count: editors.length,
        iframe_count: queryDeep(document, 'iframe').length,
        model_count: globalThis.monaco?.editor?.getModels?.().length ?? null,
        active_element: describe(active),
        editors: editors.slice(0, 3).map(root => ({
          root: describe(root),
          view_lines: queryDeep(root, '.view-lines').slice(0, 3).map(describe),
          rendered_line_count: queryDeep(root, '.view-lines .view-line').length
        })),
        fields: fields.slice(0, 30).map(describe),
        fields_truncated: fields.length > 30,
        submit_buttons: buttons.slice(0, 10).map(el => ({
          ...describe(el),
          aria_label_present: el.hasAttribute('aria-label'),
          aria_label_matches: /проверить\\s+код/i.test(el.getAttribute('aria-label') || '')
        })),
        submit_buttons_truncated: buttons.length > 10,
        output_panels: panels.slice(0, 10).map(el => ({
          ...describe(el),
          text_length: (el.textContent || '').length,
          contains_accepted: /Решение принято/.test(el.textContent || ''),
          contains_rejected: /Решение отклонено/.test(el.textContent || '')
        })),
        visible_result_labels: visibleResults
      };
    }""")
    if submission_trace is not None:
        structure["submission_trace"] = submission_trace
    structure["note"] = "Файл содержит только структуру и размеры элементов; текст задачи, код, значения полей и URL не сохраняются."
    private_json(output, structure)
    return structure

