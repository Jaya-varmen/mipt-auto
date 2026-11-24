import pytest

from mipt_auto.diagnostics import capture_editor_structure
from mipt_auto.portal import Portal

pytestmark = pytest.mark.browser


def test_editor_uses_native_edit_context_without_clipboard(page, config):
    page.goto(config.base_url)
    page.set_content('''<div class="monaco-editor" style="width:600px;height:200px;border:1px solid black">
        <div role="textbox" tabindex="0" style="position:absolute;width:1px;height:1px;opacity:0"></div></div>
        <script>
          const field = document.querySelector('[role=textbox]');
          document.querySelector('.monaco-editor').addEventListener('click', () => field.focus());
          const ec = new EditContext({text:'old code', selectionStart:0, selectionEnd:8});
          field.editContext = ec;
          ec.addEventListener('textupdate', () => {field.textContent = ec.text;});
          field.addEventListener('keydown', e => {
            if ((e.metaKey || e.ctrlKey) && e.key === 'a') {
              ec.updateSelection(0, ec.text.length); e.preventDefault();
            }
          });
        </script>''')
    config.execution['editor_kind'] = 'monaco'
    config.execution['editor'] = {'css': '.monaco-editor'}
    code = 'text = input()\nprint(text)\n'
    Portal(page, config).fill_editor(code)
    assert page.locator('[role=textbox]').evaluate('e => e.editContext.text') == code


def test_editor_waits_for_monaco_input_after_shell_appears(page, config):
    page.goto(config.base_url)
    page.set_content('''<div class="monaco-editor" style="width:600px;height:200px;border:1px solid black"></div>
        <script>
          setTimeout(() => {
            const root = document.querySelector('.monaco-editor');
            const field = document.createElement('div');
            field.setAttribute('role', 'textbox');
            field.tabIndex = 0;
            Object.assign(field.style, {position:'absolute', width:'1px', height:'1px', opacity:'0'});
            root.appendChild(field);
            root.addEventListener('click', () => field.focus());
            const ec = new EditContext({text:'', selectionStart:0, selectionEnd:0});
            field.editContext = ec;
            ec.addEventListener('textupdate', () => {field.textContent = ec.text;});
            field.addEventListener('keydown', e => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'a') {
                ec.updateSelection(0, ec.text.length); e.preventDefault();
              }
            });
          }, 700);
        </script>''')
    config.execution['editor_kind'] = 'monaco'
    config.execution['editor'] = {'css': '.monaco-editor'}
    code = 'text = input()\nprint(text)\n'
    Portal(page, config).fill_editor(code)
    assert page.locator('[role=textbox]').evaluate('e => e.editContext.text') == code


def test_editor_uses_input_outside_monaco_and_clicks_past_line_numbers(page, config):
    page.goto(config.base_url)
    page.set_content('''<div class="monaco-editor" style="position:relative;width:600px;height:200px;border:1px solid black">
          <div class="gutter" style="position:absolute;left:0;top:0;width:85px;height:180px"></div>
          <div class="view-lines" style="position:absolute;left:85px;top:0;width:500px;height:180px">
            <div class="view-line">old code</div>
          </div>
        </div>
        <textarea class="inputarea" role="textbox" style="position:absolute;width:1px;height:1px;opacity:0">old code</textarea>
        <script>
          const root = document.querySelector('.monaco-editor');
          const input = document.querySelector('textarea.inputarea');
          root.querySelector('.view-lines').addEventListener('click', () => input.focus());
          input.addEventListener('input', () => {
            const lines = root.querySelector('.view-lines');
            lines.replaceChildren(...input.value.split('\\n').map(value => {
              const line = document.createElement('div');
              line.className = 'view-line';
              line.textContent = value;
              return line;
            }));
          });
        </script>''')
    config.execution['editor_kind'] = 'monaco'
    config.execution['editor'] = {'css': '.monaco-editor'}
    code = 'group = input().strip()\nidentifier = input().strip()\nprint(group + "-" + identifier[-4:])\n'
    Portal(page, config).fill_editor(code)
    assert page.locator('textarea.inputarea').input_value() == code


def test_editor_diagnostic_does_not_save_field_values(page, config, tmp_path):
    page.goto(config.base_url)
    page.set_content('''<web-component-trenajer></web-component-trenajer>
        <textarea class="inputarea" role="textbox">secret code</textarea>
        <input type="password" value="secret password">
        <script>
          document.querySelector('web-component-trenajer').attachShadow({mode:'open'}).innerHTML =
            '<div class="monaco-editor"><div class="view-lines"><div class="view-line">secret code</div></div></div>' +
            '<button aria-label="Run code">Проверить код</button>';
        </script>''')
    output = tmp_path / 'editor-diagnostic.json'
    structure = capture_editor_structure(page, config, output)
    saved = output.read_text(encoding='utf-8')
    assert structure['editor_count'] == 1
    assert len(structure['submit_buttons']) == 1
    assert structure['submit_buttons'][0]['aria_label_matches'] is False
    assert 'secret code' not in saved
    assert 'secret password' not in saved


def test_submit_button_finds_visible_text_in_shadow_component(page, config):
    page.goto(config.base_url)
    page.set_content('''<web-component-trenajer></web-component-trenajer>
        <script>
          window.submitClicks = 0;
          const root = document.querySelector('web-component-trenajer').attachShadow({mode:'open'});
          const button = document.createElement('button');
          button.setAttribute('aria-label', 'Run code');
          button.textContent = 'Проверить код';
          button.addEventListener('click', () => window.submitClicks++);
          root.appendChild(button);
        </script>''')
    config.execution['submit'] = {'role': 'button', 'name': 'Проверить код'}
    assert page.get_by_role('button', name='Проверить код', exact=True).count() == 0
    button = Portal(page, config).submit_button()
    assert button.is_enabled()
    button.click()
    assert page.evaluate('window.submitClicks') == 1
