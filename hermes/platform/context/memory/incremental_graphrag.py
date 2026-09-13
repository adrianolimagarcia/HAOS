"""Incremental GraphRAG Updater.

Incrementally extracts entities, relations, and communities from markdown knowledge events
(e.g., Obsidian notes, DecisionStore ADRs) and applies them to GraphRAGAdapter
without wiping the existing graph.

Deterministic heuristics and regexes only: zero network/LLM dependencies.
Strictly stdlib-only.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

from hermes.platform.context.memory.events import KnowledgeEvent, KnowledgeEventBus, KnowledgeEventType
from hermes.platform.context.memory.graphrag import GraphEntity, GraphRAGAdapter, GraphRelation


# Regex patterns for deterministic extraction
_RE_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_RE_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]+))?\]\]")
_RE_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_RE_ADR = re.compile(r"\b(ADR(?:[-_][A-Z0-9]+|\s*#?\d+))\b", re.IGNORECASE)
_RE_COMPONENT = re.compile(
    r"\b([A-Z][a-zA-Z0-9]*(?:Adapter|Service|Store|Manager|Provider|Router|Engine|Bus|Gateway|Client|Handler|Agent))\b"
)
_RE_CAPITALIZED_TERMS = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:[ _][A-Z][a-zA-Z0-9]+){1,3})\b")

# Relation patterns in text lines: "X connects to Y", "X depends on Y", "X supersedes Y", "X uses Y"
_RE_RELATION_LINE = re.compile(
    r"\b([A-Za-z0-9_-]+)\s+(depends\s+on|connects\s+to|supersedes|uses|implements|calls|wraps|orchestrates|delegates\s+to|extends)\s+([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


class IncrementalGraphRAGUpdater:
    """Listens to KnowledgeEvents and incrementally updates a GraphRAGAdapter."""

    def __init__(
        self,
        graphrag_adapter: GraphRAGAdapter,
        event_bus: Optional[KnowledgeEventBus] = None,
        auto_subscribe: bool = True,
    ) -> None:
        self.adapter = graphrag_adapter
        self.event_bus = event_bus
        # Track which entities/relations originated from which URI for clean updates/deletions
        self._uri_entities: Dict[str, Set[str]] = {}
        self._uri_relations: Dict[str, Set[Tuple[str, str, str]]] = {}

        if self.event_bus is not None and auto_subscribe:
            self.event_bus.subscribe(self._on_event)

    def _on_event(self, event: KnowledgeEvent) -> None:
        """Callback invoked when event is published on event bus."""
        # Process event synchronously if received directly via subscription
        self.process_event(event)

    def process_pending_queue(self) -> int:
        """Process all queued events from the event bus."""
        if self.event_bus is None:
            return 0

        total_updates = 0
        while True:
            event = self.event_bus.get_next_event(block=False)
            if event is None:
                break
            try:
                total_updates += self.process_event(event)
            finally:
                self.event_bus.task_done()

        return total_updates

    def process_event(self, event: KnowledgeEvent) -> int:
        """Process a single KnowledgeEvent and update GraphRAGAdapter incrementally.

        Returns total number of entities and relations added/updated.
        """
        if event.event_type == KnowledgeEventType.NOTE_DELETED:
            return self._handle_deletion(event.uri)

        # For NOTE_CREATED, NOTE_MODIFIED, DECISION_RECORDED:
        entities, relations, community_id, community_summary = self._extract_knowledge(event)

        # If modifying, prune previous entities/relations exclusively tied to this URI if needed
        if event.event_type == KnowledgeEventType.NOTE_MODIFIED:
            self._prune_uri_items(event.uri, keep_entities={e.name for e in entities})

        applied_count = self._apply_to_adapter(
            uri=event.uri,
            entities=entities,
            relations=relations,
            community_id=community_id,
            community_summary=community_summary,
        )

        return applied_count

    def _handle_deletion(self, uri: str) -> int:
        """Handle deletion of a note or decision document."""
        old_entities = self._uri_entities.pop(uri, set())
        old_relations = self._uri_relations.pop(uri, set())

        removed_count = 0

        # Remove relations originating from this uri
        if old_relations:
            initial_len = len(self.adapter._relations)
            self.adapter._relations = [
                r
                for r in self.adapter._relations
                if (r.source, r.target, r.relation_type) not in old_relations
            ]
            removed_count += (initial_len - len(self.adapter._relations))

        # Remove entities if no other URI claims them
        all_other_entities: Set[str] = set()
        for other_uri, ent_set in self._uri_entities.items():
            if other_uri != uri:
                all_other_entities.update(ent_set)

        for ent_name in old_entities:
            if ent_name not in all_other_entities:
                if ent_name in self.adapter._entities:
                    del self.adapter._entities[ent_name]
                    removed_count += 1

        return removed_count

    def _prune_uri_items(self, uri: str, keep_entities: Set[str]) -> None:
        """Remove previously extracted items for this URI that are no longer present."""
        prev_entities = self._uri_entities.get(uri, set())
        entities_to_remove = prev_entities - keep_entities

        all_other_entities: Set[str] = set()
        for other_uri, ent_set in self._uri_entities.items():
            if other_uri != uri:
                all_other_entities.update(ent_set)

        for ent_name in entities_to_remove:
            if ent_name not in all_other_entities and ent_name in self.adapter._entities:
                del self.adapter._entities[ent_name]

    def _extract_knowledge(
        self, event: KnowledgeEvent
    ) -> Tuple[List[GraphEntity], List[GraphRelation], Optional[str], Optional[str]]:
        """Extracts entities, relations, and community summaries using deterministic heuristics."""
        entities_map: Dict[str, GraphEntity] = {}
        relations_list: List[GraphRelation] = []

        content = event.content or ""
        title = event.title or "Untitled"
        uri = event.uri or ""
        meta = event.metadata or {}

        # 1. Primary entity representing the document/decision itself
        primary_type = "Decision" if event.event_type == KnowledgeEventType.DECISION_RECORDED else "Note"
        primary_name = meta.get("id") or title
        primary_community = meta.get("category") or meta.get("type") or "Core"

        # Determine community id
        if "20-Architecture" in uri or "ADR" in uri or "adr" in uri.lower():
            primary_community = "Architecture"
        elif "00-Inbox" in uri or "inbox" in uri.lower():
            primary_community = "Inbox"
        elif "10-Specs" in uri or "specs" in uri.lower():
            primary_community = "Specifications"
        elif "30-Workflows" in uri or "workflows" in uri.lower():
            primary_community = "Workflows"

        # Extract frontmatter category/tags if present
        fm_match = _RE_FRONTMATTER.match(content)
        if fm_match:
            fm_text = fm_match.group(1)
            for line in fm_text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    k = k.strip().lower()
                    v = v.strip().strip("\"'")
                    if k in ("type", "category", "community") and v:
                        primary_community = v

        doc_summary = meta.get("summary") or (content[:150].strip().replace("\n", " ") if content else title)
        primary_entity = GraphEntity(
            name=primary_name,
            entity_type=primary_type,
            description=f"{primary_type} titled '{title}': {doc_summary}",
            community_id=primary_community,
        )
        entities_map[primary_name] = primary_entity

        # 2. Extract ADR references (e.g. ADR-001, ADR-CORE-01)
        for adr_match in _RE_ADR.finditer(content):
            adr_id = adr_match.group(1).upper().replace(" ", "-")
            if adr_id != primary_name:
                if adr_id not in entities_map:
                    entities_map[adr_id] = GraphEntity(
                        name=adr_id,
                        entity_type="ADR",
                        description=f"Architectural Decision Record {adr_id}",
                        community_id="Architecture",
                    )
                relations_list.append(
                    GraphRelation(
                        source=primary_name,
                        target=adr_id,
                        relation_type="references_adr",
                        description=f"{primary_name} references {adr_id}",
                    )
                )

        # 3. Extract Supersedes relationships (from metadata or text)
        supersedes = meta.get("supersedes")
        if supersedes:
            if isinstance(supersedes, str):
                supersedes_items = [supersedes]
            elif isinstance(supersedes, list):
                supersedes_items = [str(x) for x in supersedes]
            else:
                supersedes_items = []
            for target_id in supersedes_items:
                if target_id not in entities_map:
                    entities_map[target_id] = GraphEntity(
                        name=target_id,
                        entity_type="Decision",
                        description=f"Superseded Decision {target_id}",
                        community_id="Architecture",
                    )
                relations_list.append(
                    GraphRelation(
                        source=primary_name,
                        target=target_id,
                        relation_type="supersedes",
                        description=f"{primary_name} supersedes {target_id}",
                    )
                )

        # 4. Extract Component entities (e.g. ObsidianAdapter, DecisionStore, EventBus)
        for comp_match in _RE_COMPONENT.finditer(content):
            comp_name = comp_match.group(1)
            if comp_name != primary_name:
                if comp_name not in entities_map:
                    entities_map[comp_name] = GraphEntity(
                        name=comp_name,
                        entity_type="Component",
                        description=f"Software component {comp_name}",
                        community_id=primary_community,
                    )
                relations_list.append(
                    GraphRelation(
                        source=primary_name,
                        target=comp_name,
                        relation_type="mentions_component",
                        description=f"{primary_name} mentions component {comp_name}",
                    )
                )

        # 4b. Extract Capitalized Technology/Domain Entities (e.g. Kafka, Postgres, Redis, Kubernetes)
        tech_words = {"kafka", "postgres", "postgresql", "redis", "kubernetes", "docker", "docker swarm", "graphql", "rest", "grpc", "sqlite", "elasticsearch"}
        for word in tech_words:
            if re.search(rf"\b{re.escape(word)}\b", content, re.IGNORECASE):
                canon_name = word.title()
                if canon_name not in entities_map and canon_name != primary_name:
                    entities_map[canon_name] = GraphEntity(
                        name=canon_name,
                        entity_type="Technology",
                        description=f"Technology {canon_name} referenced in {title}",
                        community_id=primary_community,
                    )
                    relations_list.append(
                        GraphRelation(
                            source=primary_name,
                            target=canon_name,
                            relation_type="uses_technology",
                            description=f"{primary_name} uses technology {canon_name}",
                        )
                    )

        # 5. Extract Wikilinks [[Target|Label]] or [[Target]]
        for wl_match in _RE_WIKILINK.finditer(content):
            target_link = wl_match.group(1).strip()
            if target_link and target_link != primary_name:
                if target_link not in entities_map:
                    entities_map[target_link] = GraphEntity(
                        name=target_link,
                        entity_type="Concept",
                        description=f"Concept/Document linked via [[{target_link}]]",
                        community_id=primary_community,
                    )
                relations_list.append(
                    GraphRelation(
                        source=primary_name,
                        target=target_link,
                        relation_type="links_to",
                        description=f"{primary_name} links to {target_link}",
                    )
                )

        # 6. Extract heuristic relation lines ("X depends on Y", "X connects to Y")
        for line in content.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            for rel_match in _RE_RELATION_LINE.finditer(line_str):
                src = rel_match.group(1).strip()
                rel_verb = rel_match.group(2).strip().lower().replace(" ", "_")
                tgt = rel_match.group(3).strip()

                if src and tgt and src != tgt:
                    if src not in entities_map:
                        entities_map[src] = GraphEntity(
                            name=src,
                            entity_type="Entity",
                            description=f"Entity {src}",
                            community_id=primary_community,
                        )
                    if tgt not in entities_map:
                        entities_map[tgt] = GraphEntity(
                            name=tgt,
                            entity_type="Entity",
                            description=f"Entity {tgt}",
                            community_id=primary_community,
                        )
                    relations_list.append(
                        GraphRelation(
                            source=src,
                            target=tgt,
                            relation_type=rel_verb,
                            description=f"{src} {rel_verb} {tgt}",
                        )
                    )

        # Deduplicate relations
        unique_relations: List[GraphRelation] = []
        seen_rels: Set[Tuple[str, str, str]] = set()
        for r in relations_list:
            key = (r.source, r.target, r.relation_type)
            if key not in seen_rels:
                seen_rels.add(key)
                unique_relations.append(r)

        # Community summary generation for primary_community
        rel_types = sorted(list(set(r.relation_type for r in unique_relations)))
        community_summary = (
            f"Community '{primary_community}' includes entities such as {', '.join(list(entities_map.keys())[:5])}. "
            f"Active relations focus on {', '.join(rel_types[:4]) or 'structural references'}."
        )

        return list(entities_map.values()), unique_relations, primary_community, community_summary

    def _apply_to_adapter(
        self,
        uri: str,
        entities: List[GraphEntity],
        relations: List[GraphRelation],
        community_id: Optional[str],
        community_summary: Optional[str],
    ) -> int:
        """Incrementally applies extracted entities and relations to GraphRAGAdapter without wiping."""
        count = 0
        uri_ents = self._uri_entities.setdefault(uri, set())
        uri_rels = self._uri_relations.setdefault(uri, set())

        # Update entities
        for ent in entities:
            uri_ents.add(ent.name)
            # If entity already exists, preserve community_id if already set, update description if more detailed
            if ent.name in self.adapter._entities:
                existing = self.adapter._entities[ent.name]
                # Merge descriptions if not already contained
                if ent.description and ent.description not in existing.description:
                    existing.description = f"{existing.description}; {ent.description}"
                if existing.community_id is None and ent.community_id is not None:
                    existing.community_id = ent.community_id
            else:
                self.adapter._entities[ent.name] = ent
                count += 1

        # Update relations
        existing_rel_keys = {(r.source, r.target, r.relation_type) for r in self.adapter._relations}
        for rel in relations:
            key = (rel.source, rel.target, rel.relation_type)
            uri_rels.add(key)
            if key not in existing_rel_keys:
                self.adapter._relations.append(rel)
                existing_rel_keys.add(key)
                count += 1

        # Update community summary incrementally
        if community_id and community_summary:
            existing_comm = self.adapter._communities.get(community_id)
            if existing_comm:
                # Merge or refresh summary
                self.adapter._communities[community_id] = f"{existing_comm}\n{community_summary}"
            else:
                self.adapter._communities[community_id] = community_summary

        return count
