"""Keep secrets out of everything this tool prints.

Seeds, Pinata keys and Home Assistant tokens pass through memory only. Any
text that reaches the terminal — including error messages that echo a server
response — goes through a Redactor first, so a secret that slipped into such
a message is masked rather than shown.
"""

MASK = "‹hidden›"


class Redactor:
    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, *secrets: str) -> None:
        for secret in secrets:
            # Very short values would mask ordinary words; nothing we protect is
            # short. A mnemonic is masked as a whole phrase: its single words are
            # ordinary English words.
            if secret and len(secret) >= 8:
                self._secrets.add(secret)

    def __call__(self, text: object) -> str:
        result = str(text)
        for secret in sorted(self._secrets, key=len, reverse=True):
            result = result.replace(secret, MASK)
        return result


REDACT = Redactor()
