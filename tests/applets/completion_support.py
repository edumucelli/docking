"""Entry completion test double for the conversion dialogs."""


class FakeEntryCompletion:
    def __init__(self) -> None:
        self.model = None
        self.text_column = None
        self.inline_completion = False
        self.popup_completion = False
        self.match_func = None

    def set_model(self, model) -> None:
        self.model = model

    def get_model(self):
        return self.model

    def set_text_column(self, column: int) -> None:
        self.text_column = column

    def set_inline_completion(self, value: bool) -> None:
        self.inline_completion = value

    def set_popup_completion(self, value: bool) -> None:
        self.popup_completion = value

    def set_match_func(self, func, user_data) -> None:
        self.match_func = lambda completion, key, tree_iter, _data: func(
            completion,
            key,
            tree_iter,
            user_data,
        )
