package com.bbdwz.emailservice.service;

import com.bbdwz.emailservice.config.MailAccountProperties;
import com.bbdwz.emailservice.event.MailReceivedEvent;
import com.bbdwz.emailservice.model.ReceivedMail;
import com.bbdwz.emailservice.model.SendMailRequest;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.*;

@Service
public class ForwardingService {
    private static final int MAX_EXECUTIONS = 200;

    public record Rule(String id, boolean enabled, String sourceAccountId,
                       String sendAccountId, List<String> recipients) {
        public Rule {
            id = id == null || id.isBlank() ? UUID.randomUUID().toString() : id;
            recipients = recipients == null ? List.of() : List.copyOf(recipients);
        }
    }

    public record Execution(String id, String ruleId, Instant executedAt, String status,
                            String sourceAccountId, String sendAccountId, String from,
                            String subject, List<String> recipients, String error) {}

    public static class State {
        public String defaultAccountId;
        public List<Rule> rules = new ArrayList<>();
        public List<Execution> executions = new ArrayList<>();
    }

    private final Path configPath;
    private final ObjectMapper mapper;
    private final MailAccountService mailService;
    private State state;

    public ForwardingService(MailAccountProperties properties, ObjectMapper mapper, MailAccountService mailService) {
        this.configPath = Path.of(properties.getForwardingConfig()).toAbsolutePath().normalize();
        this.mapper = mapper;
        this.mailService = mailService;
        this.state = load();
    }

    public synchronized String getDefaultAccountId() {
        if (mailService.isAvailable(state.defaultAccountId)) return state.defaultAccountId;
        return mailService.firstAvailableAccountId();
    }

    public synchronized String setDefaultAccountId(String accountId) {
        if (!mailService.isAvailable(accountId)) throw new IllegalArgumentException("unknown, disabled, or unconfigured account");
        state.defaultAccountId = accountId;
        save();
        return accountId;
    }

    public synchronized List<Rule> getRules() { return List.copyOf(state.rules); }
    public synchronized List<Execution> getExecutions(int limit) {
        int size = state.executions.size();
        List<Execution> result = new ArrayList<>(state.executions.subList(
                Math.max(0, size - Math.max(1, Math.min(limit, MAX_EXECUTIONS))), size));
        Collections.reverse(result);
        return List.copyOf(result);
    }

    public synchronized Rule createRule(Rule input) {
        Rule rule = normalize(new Rule(UUID.randomUUID().toString(), input.enabled(), input.sourceAccountId(), input.sendAccountId(), input.recipients()));
        state.rules.add(rule);
        save();
        return rule;
    }

    public synchronized Rule updateRule(String id, Rule input) {
        for (int i = 0; i < state.rules.size(); i++) {
            if (state.rules.get(i).id().equals(id)) {
                Rule rule = normalize(new Rule(id, input.enabled(), input.sourceAccountId(), input.sendAccountId(), input.recipients()));
                state.rules.set(i, rule);
                save();
                return rule;
            }
        }
        throw new IllegalArgumentException("forwarding rule not found");
    }

    public synchronized void deleteRule(String id) {
        if (!state.rules.removeIf(rule -> rule.id().equals(id))) throw new IllegalArgumentException("forwarding rule not found");
        save();
    }

    @EventListener
    public void onMail(MailReceivedEvent event) {
        ReceivedMail incoming = event.getMail();
        if (incoming.locallyForwarded()) return;
        List<Rule> matching;
        synchronized (this) {
            matching = state.rules.stream().filter(rule -> rule.enabled() && rule.sourceAccountId().equals(incoming.accountId())).toList();
        }
        for (Rule rule : matching) execute(rule, incoming);
    }

    private void execute(Rule rule, ReceivedMail incoming) {
        String status = "success";
        String error = null;
        try {
            SendMailRequest outgoing = new SendMailRequest();
            outgoing.setAccountId(rule.sendAccountId());
            outgoing.setTo(rule.recipients());
            outgoing.setSubject("Fwd: " + incoming.subject());
            outgoing.setText("原发件人: " + incoming.from() + "\n原收件时间: " + incoming.receivedAt() + "\n\n" + incoming.text());
            mailService.send(outgoing, true);
        } catch (Exception e) {
            status = "failed";
            error = Objects.toString(e.getMessage(), e.getClass().getSimpleName());
        }
        synchronized (this) {
            state.executions.add(new Execution(UUID.randomUUID().toString(), rule.id(), Instant.now(), status,
                    incoming.accountId(), rule.sendAccountId(), incoming.from(), incoming.subject(), rule.recipients(), error));
            if (state.executions.size() > MAX_EXECUTIONS) {
                state.executions = new ArrayList<>(state.executions.subList(state.executions.size() - MAX_EXECUTIONS, state.executions.size()));
            }
            save();
        }
    }

    private Rule normalize(Rule rule) {
        if (!mailService.isAvailable(rule.sourceAccountId())) throw new IllegalArgumentException("invalid source account");
        if (!mailService.isAvailable(rule.sendAccountId())) throw new IllegalArgumentException("invalid sending account");
        if (rule.recipients().isEmpty()) throw new IllegalArgumentException("at least one forwarding recipient is required");
        return rule;
    }

    private State load() {
        if (!Files.exists(configPath)) return new State();
        try {
            JsonNode root = mapper.readTree(configPath.toFile());
            if (root.has("rules")) return mapper.treeToValue(root, State.class);
            State migrated = new State();
            root.fields().forEachRemaining(entry -> {
                JsonNode old = entry.getValue();
                List<String> recipients = new ArrayList<>();
                old.path("recipients").forEach(item -> recipients.add(item.asText()));
                migrated.rules.add(new Rule(UUID.randomUUID().toString(), old.path("enabled").asBoolean(false),
                        entry.getKey(), old.path("sendAccountId").asText(entry.getKey()), recipients));
            });
            this.state = migrated;
            save();
            return migrated;
        } catch (IOException e) { throw new IllegalStateException("failed to load mail settings", e); }
    }

    private void save() {
        try {
            Path parent = configPath.getParent();
            Files.createDirectories(parent);
            Path temp = Files.createTempFile(parent, "mail-settings-", ".json");
            mapper.writerWithDefaultPrettyPrinter().writeValue(temp.toFile(), state);
            Files.move(temp, configPath, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) { throw new IllegalStateException("failed to save mail settings", e); }
    }
}
