# Arquitetura

Visão geral simplificada dos componentes principais e fluxos de eventos.

```mermaid
flowchart LR
  Client[Cliente] --> EventBus[Event Bus]
  EventBus --> ReplayStore[Replay Store]
  ReplayStore --> AdapterSupervisor[Adapter Supervisor]
  AdapterSupervisor --> Adapters[Adapters]
  Adapters --> Viewer[Viewer]
```

Descreva aqui os componentes em detalhe (event bus, agregadores de sessão, replay store, adaptadores).

## Ciclo de vida do evento (detalhado)

```mermaid
flowchart LR
  Client["Cliente / Fonte"] --> Ingest["Ingest / Validação"]
  Ingest --> EventBus["Event Bus / Broker"]
  EventBus --> Enricher["Enriquecedor / Normalizador"]
  Enricher --> ReplayStore["Replay Store / Persistência"]
  ReplayStore --> SessionAggregator["Session Aggregator"]
  SessionAggregator --> AdapterSupervisor["Adapter Supervisor"]
  AdapterSupervisor --> Adapters["Adaptadores (OTel, debug, file, ...)"]
  Adapters --> Viewer["Viewer / UI"]
```

## Agregação de sessão (sequência)

```mermaid
sequenceDiagram
  participant C as Cliente
  participant EB as EventBus
  participant SA as SessionAggregator
  participant RS as ReplayStore
  participant AS as AdapterSupervisor
  participant AD as Adapter
  participant V as Viewer

  C->>EB: envia EventEnvelope
  EB->>SA: encaminha evento
  SA->>SA: agrega estado da sessão
  SA->>RS: persiste snapshot / evento
  SA->>AS: notifica adaptadores relevantes
  AS->>AD: despacha para adaptador
  AD->>V: atualiza/entrega para o viewer
```

## Fluxo de replay

```mermaid
flowchart LR
  User[Requisição de replay] --> ReplayStore
  ReplayStore --> Streamer[Streamer de eventos]
  Streamer --> AdapterSupervisor
  AdapterSupervisor --> Adapters
  Adapters --> Viewer
```

## Mensagens / Contratos (visão simplificada)

```mermaid
classDiagram
  class EventEnvelope {
    +string provider_id
    +string session_id
    +datetime timestamp
    +AgentEvent event
  }

  class AgentEvent {
    <<discriminated union>>
    ToolStart\nToolEnd\nUserTurn\nSubagentStart\nSubagentEnd\nProgress\nSessionStart\nSessionEnd\nTokenUsage
  }

  class ViewerMessage {
    <<union>>
    AgentCreatedMessage\nAgentClosedMessage\nAgentToolStartMessage\nAgentToolDoneMessage\nAgentStatusMessage
  }

  EventEnvelope --> AgentEvent
  ViewerMessage <.. EventEnvelope : projector maps events -> viewer messages
```

> Nota: os nomes de eventos e mensagens são derivados de `app/protocol/domain_events.py` e `app/protocol/viewer_messages.py`.

Esses diagramas usam o plugin `mkdocs-mermaid2-plugin`; ao visualizar localmente com `mkdocs serve` o mermaid será renderizado automaticamente.
