# Parte 1 — extração por submissão e revisão de acesso

Atualização: 22/11/2026. Base anterior: `75636b4` (integração B09).

## Responsabilidades

- O principal mantém contas, perfis, vínculos acadêmicos, banca, documentos B12 e repositório público.
- Este serviço mantém o PDF original, seu SHA-256, histórico e texto privado de cada submissão.
- `ExtracaoPDF` identifica uma versão por UUID, monografia remota, caminho imutável e SHA-256. Reenvios conservam o arquivo anterior e geram outro registro.
- `submeter` persiste status e tarefa na mesma transação. Não abre AMQP nem extrai texto durante o HTTP. O lock PostgreSQL serializa a submissão; a espera do extrator não bloqueia nova versão após pedido de ajustes.
- `processar_extracoes` publica `monografia.submetida` e consome a fila configurada por `EXTRACOES_FILA` (padrão `monografias.extrair`). A topologia usa os sufixos `.eventos`, `.falhas` e `.dlq`.
- O contrato contém `versao=1`, `evento_id`, `monografia_id` remoto e `sha256`. Data e arquivo são resolvidos na outbox local; nenhum caminho ou JWT é aceito da mensagem. O vínculo `origem_id` continua identificando o principal.
- Publicação persistente com confirmação do broker; ACK após commit do índice; repetição do UUID concluído não reaplica o resultado. Erro de envio mantém a tarefa e agenda nova tentativa em 30 segundos. Incerteza após envio permite nova entrega depois de cinco minutos.
- Erros de processamento vão para DLQ e são reprogramados com espera crescente, até três tentativas. O erro armazenado é somente o nome da classe, sem dados sensíveis. `--repetir-falhas` permite retomada deliberada.
- PDF sem texto finaliza com `sem_texto=True`. O texto é limitado a 1.000.000 de caracteres; OCR não faz parte da entrega.

## Integridade e autorização

- Upload valida extensão, tamanho, cabeçalho, estrutura, páginas e ausência de senha. A validação estrutural não extrai o texto.
- Aprovação e download verificam o SHA-256. O download entrega os mesmos bytes verificados, sem reabrir o arquivo depois da conferência.
- Escritas bloqueiam a monografia no banco e reavaliam seu estado; somente o aluno proprietário edita rascunhos ou ajustes. Exclusão é limitada a rascunhos nunca submetidos e sem vínculo com o principal.
- Criação exige curso e orientador compatíveis com os claims assinados. Alterações desses vínculos também exigem correspondência com o token. O aluno vem do token; registros vinculados exigem `integracao:origem`.
- Coordenadores consultam somente `curso_ids` autorizados. Não alteram rascunhos de alunos por esta API.
- `GET /api/monografias/<id>/extracoes/` retorna `{"versoes": [...]}` somente ao aluno/orientador autorizado. Texto privado e estado não integram o catálogo público. Não há rota pública para `/media/`.
- `/admin/` exige `ADMIN_ALLOWED_NETWORKS`; padrão somente loopback. Cabeçalhos encaminhados não são aceitos. No Docker, o endereço visto pode ser o gateway: use o admin do principal para gestão de contas, sem liberar toda a rede Docker por conveniência.

O comando de desenvolvimento `gerar_token` agora exige `--curso-id` e `--orientador-id` para aluno, e `--curso-autorizado` para coordenador. Não emite `integracao:origem`.

## Migrações e operação

```bash
docker compose build web consumer extractor
docker compose run --rm web python manage.py migrate --noinput
docker compose up -d web consumer extractor
docker compose exec extractor python manage.py status_extracoes
docker compose exec extractor python manage.py processar_extracoes --uma-vez --repetir-falhas
```

`0005` cria a outbox/índice privado. `0006` prepara extração da versão atual de registros já submetidos, excluindo rascunhos/ajustes. Não inventa versões antigas que não foram registradas. Em produção, migrations devem terminar antes de iniciar workers.

O Compose monta o mesmo volume privado `media` no web e no extrator. Faça backup coordenado de PostgreSQL e desse volume. Não apague PDFs de versões presentes em `ExtracaoPDF`; o banco os protege por vínculo, mas exclusões manuais no sistema de arquivos continuam detectáveis apenas pelo hash/ausência do arquivo.

## Evidências de teste

- **20 testes aprovados** no PostgreSQL isolado: autenticação JWT, escopo de origem, vínculo idempotente, acesso por objeto/curso, adulteração de PDF, extração sem texto, duas versões, repetição, falha temporária e fila indisponível.
- `makemigrations --check --dry-run`: sem alterações.
- Teste integrado no principal: Chrome enviou duas versões, o worker real consumiu ambas via RabbitMQ, o orientador aprovou por HTTP/JWT; a banca foi proposta/confirmada, avisos chegaram ao SMTP, avaliações fecharam e B12/B13 publicaram. Sem alteração direta de estados ou horários no banco.
- Detalhes do percurso e capturas estão em `docs/DESENVOLVIMENTO.md` do principal, seção Parte 1.

## Ambiente de demonstração reproduzível

`compose.integracao.yml` é exclusivo de testes, com banco, Redis e RabbitMQ próprios e API em `127.0.0.1:8011`. As senhas fixas desse arquivo são somente dos serviços descartáveis sem portas externas; as chaves Django/JWT são exigidas pelo ambiente.

```bash
export P1_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
export P1_JWT_SIGNING_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
docker compose -f compose.integracao.yml up --build -d --wait
```

No mesmo shell, no principal, configure seu RabbitMQ e Mailpit locais e execute:

```bash
RUN_P1_INTEGRATION=1 P1_MICROSERVICO_URL=http://127.0.0.1:8011/api/ env/bin/python manage.py test usuarios.test_parte1_integrada --keepdb --noinput
```

O teste aguarda de dois a três minutos pelo horário real da defesa. As contas, termos e PDFs são fictícios. Recrie o ambiente descartável entre campanhas que reiniciem sequências do banco principal, pois IDs de origem remotos são únicos.

Após usar o ambiente descartável (comando remove somente seus dados de teste):

```bash
docker compose -f compose.integracao.yml down -v
```

## Limites de escopo

Eventos antigos de aprovação/ajustes/status ainda usam o publicador legado; sua padronização completa pertence à Parte 2. A outbox obrigatória de submissão está implementada. O principal mantém sua própria outbox de avisos e a indexação pública do PDF final. Não existe importação automática de monografias históricas sem `origem_id`.
