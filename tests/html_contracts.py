"""Small stdlib DOM helper for current shell/card assertions (no browser emulation)."""
from html.parser import HTMLParser


class Element:
    def __init__(self, tag="root", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    @property
    def text(self):
        return "".join(c.text if isinstance(c, Element) else c for c in self.children)

    def findall(self, tag=None, cls=None, **attrs):
        found = []
        for child in self.children:
            if not isinstance(child, Element):
                continue
            if ((tag is None or child.tag == tag)
                    and (cls is None or cls in child.attrs.get("class", "").split())
                    and all(k in child.attrs and (v is None or child.attrs[k] == v)
                            for k, v in attrs.items())):
                found.append(child)
            found.extend(child.findall(tag, cls, **attrs))
        return found

    def one(self, tag=None, cls=None, **attrs):
        found = self.findall(tag, cls, **attrs)
        assert len(found) == 1, (tag, cls, attrs, len(found))
        return found[0]


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.root = Element()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        element = Element(tag, attrs)
        self.stack[-1].children.append(element)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                       "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def document(response):
    parser = _Parser()
    parser.feed(response.get_data(as_text=True))
    return parser.root


def assert_mobile_drawer(test, response):
    test.assertEqual(response.status_code, 200)
    root = document(response)
    navigation = root.one(**{"data-mobile-navigation": None})
    drawer = navigation.one(**{"data-mobile-drawer": None})
    test.assertIn("hidden", drawer.attrs)
    test.assertIn("inert", drawer.attrs)
    for mode in ("nodes", "menu"):
        view = drawer.one(**{"data-drawer-view": mode})
        test.assertIn("hidden", view.attrs)
        test.assertIn("inert", view.attrs)
    dock = navigation.one("nav", "neo-mobile-bottom")
    test.assertEqual([e.text.strip() for e in dock.children if isinstance(e, Element)],
                     ["Home", "Nodes", "Menu"])
    for attr in ("data-drawer-nodes", "data-drawer-toggle"):
        control = dock.one("button", **{attr: None})
        test.assertEqual(control.attrs["aria-controls"], drawer.attrs["id"])
        test.assertEqual(control.attrs["aria-expanded"], "false")
    test.assertFalse(drawer.findall(**{"data-operational-board-toggle": None}))
    test.assertFalse(root.findall(**{"data-mobile-account-menu": None}))
    return root, drawer, dock
