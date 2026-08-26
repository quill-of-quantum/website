package com.bbdwz.emailservice.controller;

import com.bbdwz.emailservice.model.SendMailRequest;
import com.bbdwz.emailservice.service.ForwardingService;
import com.bbdwz.emailservice.service.MailAccountService;
import jakarta.validation.Valid;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequestMapping("/api/mail")
public class MailController {
    private final MailAccountService mailService;
    private final ForwardingService forwardingService;

    public MailController(MailAccountService mailService, ForwardingService forwardingService) {
        this.mailService = mailService;
        this.forwardingService = forwardingService;
    }

    @GetMapping("/accounts")
    public Map<String, Object> accounts() {
        return Map.of(
                "accounts", mailService.accountStatuses(),
                "defaultAccountId", forwardingService.getDefaultAccountId(),
                "forwardingRules", forwardingService.getRules(),
                "forwardingExecutions", forwardingService.getExecutions(100)
        );
    }

    @PostMapping("/send")
    public ResponseEntity<Map<String, String>> send(@Valid @RequestBody SendMailRequest request) {
        mailService.send(request);
        return ResponseEntity.ok(Map.of("status", "sent", "accountId", request.getAccountId()));
    }

    @PostMapping("/send/default")
    public ResponseEntity<Map<String, String>> sendDefault(@Valid @RequestBody SendMailRequest request) {
        request.setAccountId(forwardingService.getDefaultAccountId());
        mailService.send(request);
        return ResponseEntity.ok(Map.of("status", "sent", "accountId", request.getAccountId()));
    }

    @PutMapping("/default-account")
    public Map<String, String> defaultAccount(@RequestBody Map<String, String> body) {
        return Map.of("defaultAccountId", forwardingService.setDefaultAccountId(body.get("accountId")));
    }

    @GetMapping("/accounts/{accountId}/messages")
    public Map<String, Object> messages(@PathVariable String accountId,
                                        @RequestParam(defaultValue = "20") int limit) {
        return Map.of("accountId", accountId, "messages", mailService.listMessages(accountId, limit));
    }

    @GetMapping("/accounts/{accountId}/messages/{uid}")
    public Map<String, Object> message(@PathVariable String accountId, @PathVariable long uid) {
        return Map.of("accountId", accountId, "message", mailService.getMessage(accountId, uid));
    }

    @PostMapping("/accounts/{accountId}/test")
    public Map<String, Object> test(@PathVariable String accountId) {
        return Map.of("accountId", accountId, "result", mailService.testAccount(accountId));
    }

    @PostMapping("/forwarding")
    public Map<String, Object> createForwarding(@RequestBody ForwardingService.Rule rule) {
        return Map.of("rule", forwardingService.createRule(rule));
    }

    @PutMapping("/forwarding/{ruleId}")
    public Map<String, Object> updateForwarding(@PathVariable String ruleId,
                                                @RequestBody ForwardingService.Rule rule) {
        return Map.of("rule", forwardingService.updateRule(ruleId, rule));
    }

    @DeleteMapping("/forwarding/{ruleId}")
    public Map<String, Object> deleteForwarding(@PathVariable String ruleId) {
        forwardingService.deleteRule(ruleId);
        return Map.of("deleted", true, "ruleId", ruleId);
    }

    @ExceptionHandler({IllegalArgumentException.class, IllegalStateException.class})
    public ResponseEntity<Map<String, String>> handle(RuntimeException error) {
        return ResponseEntity.badRequest().body(Map.of("error", error.getMessage()));
    }
}
