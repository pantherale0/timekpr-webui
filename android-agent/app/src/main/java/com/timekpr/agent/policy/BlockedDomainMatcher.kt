package com.timekpr.agent.policy

/**
 * Fast suffix-aware domain matcher for large blocklists (exact + subdomain blocking).
 * Uses a reversed-label trie built once per policy revision; lookups avoid per-query set rebuilds.
 */
class BlockedDomainMatcher private constructor(
    private val root: TrieNode,
    val domainCount: Int,
) {
    private class TrieNode {
        val children: HashMap<String, TrieNode> = HashMap(2)
        var blocked: Boolean = false
    }

    fun isEmpty(): Boolean = domainCount == 0

    fun isBlocked(queryDomain: String): Boolean {
        val labels = labelBuffer.get() ?: return false
        populateLabels(queryDomain, labels)
        if (labels.isEmpty()) return false
        for (start in labels.indices) {
            if (matchesSuffix(labels, start)) return true
        }
        return false
    }

    private fun matchesSuffix(labels: List<String>, startIndex: Int): Boolean {
        var node = root
        for (i in labels.size - 1 downTo startIndex) {
            node = node.children[labels[i]] ?: return false
        }
        return node.blocked
    }

    companion object {
        val EMPTY: BlockedDomainMatcher = BlockedDomainMatcher(TrieNode(), 0)

        private val labelBuffer = ThreadLocal.withInitial { ArrayList<String>(8) }

        fun from(domains: Collection<String>): BlockedDomainMatcher {
            if (domains.isEmpty()) return EMPTY
            val root = TrieNode()
            var count = 0
            val scratch = ArrayList<String>(8)
            for (domain in domains) {
                populateLabels(domain, scratch)
                if (scratch.isEmpty()) continue
                insert(root, scratch)
                count++
            }
            return BlockedDomainMatcher(root, count)
        }

        private fun insert(root: TrieNode, labels: List<String>) {
            var node = root
            for (index in labels.size - 1 downTo 0) {
                val label = labels[index]
                node = node.children.getOrPut(label) { TrieNode() }
            }
            node.blocked = true
        }

        private fun populateLabels(domain: String, out: MutableList<String>) {
            out.clear()
            val normalized = domain.trim().lowercase().trimEnd('.')
            if (normalized.isEmpty()) return

            var start = 0
            while (start <= normalized.length) {
                val dot = normalized.indexOf('.', start)
                val end = if (dot < 0) normalized.length else dot
                if (end > start) {
                    out.add(normalized.substring(start, end))
                }
                if (dot < 0) break
                start = dot + 1
            }
        }
    }
}
