"""Self-contained Aho–Corasick Trie, failure links and output propagation."""
from collections import deque


class Automaton:
    def __init__(self, words):
        self.edges = [{}]
        self.fail = [0]
        self.outputs = [[]]
        for word, payload in words:
            if not word:
                raise ValueError("敏感词不能为空")
            state = 0
            for char in word:
                if char not in self.edges[state]:
                    self.edges[state][char] = len(self.edges)
                    self.edges.append({})
                    self.fail.append(0)
                    self.outputs.append([])
                state = self.edges[state][char]
            self.outputs[state].append((len(word), payload))
        queue = deque(self.edges[0].values())
        while queue:
            parent = queue.popleft()
            for char, child in self.edges[parent].items():
                queue.append(child)
                state = self.fail[parent]
                while state and char not in self.edges[state]:
                    state = self.fail[state]
                self.fail[child] = self.edges[state].get(char, 0)
                self.outputs[child].extend(self.outputs[self.fail[child]])

    def search(self, text):
        state = 0
        for index, char in enumerate(text):
            while state and char not in self.edges[state]:
                state = self.fail[state]
            state = self.edges[state].get(char, 0)
            for length, payload in self.outputs[state]:
                yield index + 1 - length, index + 1, payload
