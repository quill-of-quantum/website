package com.bbdwz.emailservice.model;

import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.NotBlank;

import java.util.ArrayList;
import java.util.List;

public class SendMailRequest {
    private String accountId;
    private List<@Email String> to = new ArrayList<>();
    private List<@Email String> cc = new ArrayList<>();
    private List<@Email String> bcc = new ArrayList<>();
    @NotBlank private String subject;
    @NotBlank private String text;
    private String html;

    public String getAccountId() { return accountId; }
    public void setAccountId(String accountId) { this.accountId = accountId; }
    public List<String> getTo() { return to; }
    public void setTo(List<String> to) { this.to = to == null ? new ArrayList<>() : to; }
    public List<String> getCc() { return cc; }
    public void setCc(List<String> cc) { this.cc = cc == null ? new ArrayList<>() : cc; }
    public List<String> getBcc() { return bcc; }
    public void setBcc(List<String> bcc) { this.bcc = bcc == null ? new ArrayList<>() : bcc; }
    public String getSubject() { return subject; }
    public void setSubject(String subject) { this.subject = subject; }
    public String getText() { return text; }
    public void setText(String text) { this.text = text; }
    public String getHtml() { return html; }
    public void setHtml(String html) { this.html = html; }
}
