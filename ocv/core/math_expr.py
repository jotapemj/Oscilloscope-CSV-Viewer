"""MATH channel expression parser and evaluator."""
import numpy as np


# =============================
# MATH expression parser + evaluator
# =============================
# Gramática mínima:
#   expr   := term (('+' | '-') term)*
#   term   := unary (('*' | '/') unary)*
#   unary  := '-' unary | primary
#   primary:= NUMBER | REF | FUNC '(' expr (',' expr)? ')' | '(' expr ')'
#   REF    := CH<digits> | M<digits>    (1-based)
#   FUNC   := INT | DIFF | ABS | POW | SQRT | MIN | MAX
#
# AST nodes: tuples.
#   ('num', value)
#   ('ref', kind, idx)     kind in ('CH','M'), idx 0-based
#   ('bin', op, l, r)
#   ('neg', x)
#   ('fn1', name, x)
#   ('fn2', name, a, b)

import re as _re_math

_MATH_TOKEN_RE = _re_math.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<ref>(?:CH|M)\d+)"
    r"|(?P<id>[A-Z]+)"
    r"|(?P<op>[+\-*/(),]))"
)

_MATH_FN1 = {"INT", "DIFF", "ABS", "POW", "SQRT"}
_MATH_FN2 = {"MIN", "MAX"}


def math_tokenize(expr):
    """Tokeniza la expresión. Lanza ValueError con mensaje útil."""
    s = expr.upper().replace(" ", "")
    tokens = []
    i = 0
    src = expr.upper()
    while i < len(src):
        if src[i].isspace():
            i += 1
            continue
        m = _MATH_TOKEN_RE.match(src, i)
        if not m or m.start() != i:
            raise ValueError(f"Unexpected character at position {i}: '{src[i]}'")
        if m.group("num"):
            tokens.append(("num", float(m.group("num"))))
        elif m.group("ref"):
            r = m.group("ref")
            kind = "CH" if r.startswith("CH") else "M"
            idx = int(r[len(kind):]) - 1
            if idx < 0:
                raise ValueError(f"Invalid reference: {r}")
            tokens.append(("ref", kind, idx))
        elif m.group("id"):
            name = m.group("id")
            if name in _MATH_FN1 or name in _MATH_FN2:
                tokens.append(("fn", name))
            else:
                raise ValueError(f"Unknown identifier: {name}")
        elif m.group("op"):
            tokens.append(("op", m.group("op")))
        i = m.end()
    return tokens


class _MathParser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def eat(self):
        t = self.peek()
        self.pos += 1
        return t

    def expect_op(self, op):
        t = self.peek()
        if not t or t[0] != "op" or t[1] != op:
            raise ValueError(f"Expected '{op}'")
        self.eat()

    def parse(self):
        node = self.parse_expr()
        if self.peek() is not None:
            raise ValueError("Unexpected trailing tokens")
        return node

    def parse_expr(self):
        node = self.parse_term()
        while True:
            t = self.peek()
            if t and t[0] == "op" and t[1] in ("+", "-"):
                self.eat()
                r = self.parse_term()
                node = ("bin", t[1], node, r)
            else:
                break
        return node

    def parse_term(self):
        node = self.parse_unary()
        while True:
            t = self.peek()
            if t and t[0] == "op" and t[1] in ("*", "/"):
                self.eat()
                r = self.parse_unary()
                node = ("bin", t[1], node, r)
            else:
                break
        return node

    def parse_unary(self):
        t = self.peek()
        if t and t[0] == "op" and t[1] == "-":
            self.eat()
            return ("neg", self.parse_unary())
        return self.parse_primary()

    def parse_primary(self):
        t = self.peek()
        if t is None:
            raise ValueError("Unexpected end of expression")
        if t[0] == "num":
            self.eat()
            return ("num", t[1])
        if t[0] == "ref":
            self.eat()
            return ("ref", t[1], t[2])
        if t[0] == "fn":
            self.eat()
            fname = t[1]
            self.expect_op("(")
            a = self.parse_expr()
            if fname in _MATH_FN2:
                self.expect_op(",")
                b = self.parse_expr()
                self.expect_op(")")
                return ("fn2", fname, a, b)
            else:
                self.expect_op(")")
                return ("fn1", fname, a)
        if t[0] == "op" and t[1] == "(":
            self.eat()
            node = self.parse_expr()
            self.expect_op(")")
            return node
        raise ValueError(f"Unexpected token: {t}")


def math_parse(expr):
    """Parsea y devuelve AST. Lanza ValueError si inválida."""
    if not expr or not expr.strip():
        raise ValueError("Empty expression")
    tokens = math_tokenize(expr)
    return _MathParser(tokens).parse()


def math_deps(ast):
    """Devuelve (ch_indices_set, m_indices_set) del AST."""
    chs = set()
    ms = set()

    def walk(n):
        tag = n[0]
        if tag == "num":
            return
        if tag == "ref":
            if n[1] == "CH":
                chs.add(n[2])
            else:
                ms.add(n[2])
            return
        if tag == "neg":
            walk(n[1]); return
        if tag == "bin":
            walk(n[2]); walk(n[3]); return
        if tag == "fn1":
            walk(n[2]); return
        if tag == "fn2":
            walk(n[2]); walk(n[3]); return

    walk(ast)
    return chs, ms


def math_eval(ast, time, ch_arrays, m_arrays):
    """Evalúa el AST. ch_arrays: dict idx -> np.ndarray. m_arrays: idx -> np.ndarray.
    time: np.ndarray común. Devuelve np.ndarray del mismo tamaño que time."""
    def ev(n):
        tag = n[0]
        if tag == "num":
            return np.full_like(time, n[1], dtype=float)
        if tag == "ref":
            src = ch_arrays if n[1] == "CH" else m_arrays
            arr = src.get(n[2])
            if arr is None:
                raise ValueError(f"Missing data for {n[1]}{n[2]+1}")
            return arr
        if tag == "neg":
            return -ev(n[1])
        if tag == "bin":
            a = ev(n[2]); b = ev(n[3])
            if n[1] == "+": return a + b
            if n[1] == "-": return a - b
            if n[1] == "*": return a * b
            if n[1] == "/":
                with np.errstate(divide="ignore", invalid="ignore"):
                    out = np.where(b != 0, a / b, 0.0)
                return out
        if tag == "fn1":
            x = ev(n[2])
            fn = n[1]
            if fn == "ABS":  return np.abs(x)
            if fn == "POW":  return x * x
            if fn == "SQRT":
                with np.errstate(invalid="ignore"):
                    return np.sqrt(np.maximum(x, 0.0))
            if fn == "INT":
                # integral acumulada (trapezoidal)
                return np.concatenate(([0.0], np.cumsum(0.5 * (x[1:] + x[:-1]) * np.diff(time))))
            if fn == "DIFF":
                # diferencia centrada, bordes con forward/backward
                return np.gradient(x, time)
        if tag == "fn2":
            a = ev(n[2]); b = ev(n[3])
            if n[1] == "MIN": return np.minimum(a, b)
            if n[1] == "MAX": return np.maximum(a, b)
        raise ValueError(f"Bad node: {n}")
    return ev(ast)
