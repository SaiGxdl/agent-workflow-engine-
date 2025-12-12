from nodes import extract_functions

def test_extract_functions_simple():
    code = "def a():\\n    pass\\n"
    state = {'code': code}
    new_state = extract_functions(state)
    assert len(new_state['functions']) == 1
    assert new_state['functions'][0]['name'] == 'a'
