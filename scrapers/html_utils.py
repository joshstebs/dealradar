"""Small bounded DOM for feed extraction; no browser or optional HTML dependency."""
from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def descendants(self):
        for child in self.children:
            if isinstance(child, Node):
                yield child
                yield from child.descendants()

    def text(self):
        return " ".join(child.text() if isinstance(child, Node) else child for child in self.children).strip()

    def has_class(self, *classes):
        return bool(set(self.attrs.get("class", "").split()) & set(classes))


class Document(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        if len(html) > 2_000_000:
            raise ValueError("HTML exceeds parser limit")
        self.root = Node("root")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if len(self.stack) > 100:
            raise ValueError("HTML nesting exceeds parser limit")
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack[-1].tag not in ("script", "style"):
            self.stack[-1].children.append(data)


def plain_text(html):
    return Document(html).root.text()
