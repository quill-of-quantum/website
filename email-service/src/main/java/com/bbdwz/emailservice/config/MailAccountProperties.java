package com.bbdwz.emailservice.config;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

import java.util.LinkedHashMap;
import java.util.Map;

@Validated
@ConfigurationProperties(prefix = "mail")
public class MailAccountProperties {
    @Valid private Map<String, Account> accounts = new LinkedHashMap<>();
    @NotBlank private String forwardingConfig = "data/forwarding.json";

    public Map<String, Account> getAccounts() { return accounts; }
    public void setAccounts(Map<String, Account> accounts) { this.accounts = accounts; }
    public String getForwardingConfig() { return forwardingConfig; }
    public void setForwardingConfig(String forwardingConfig) { this.forwardingConfig = forwardingConfig; }

    public static class Account {
        private boolean enabled = true;
        private String displayName = "邮箱";
        private String address;
        private String username;
        private String password;
        @Valid private Imap imap = new Imap();
        @Valid private Smtp smtp = new Smtp();

        public boolean isEnabled() { return enabled; }
        public void setEnabled(boolean enabled) { this.enabled = enabled; }
        public String getDisplayName() { return displayName; }
        public void setDisplayName(String displayName) { this.displayName = displayName; }
        public String getAddress() { return address; }
        public void setAddress(String address) { this.address = address; }
        public String getUsername() { return username; }
        public void setUsername(String username) { this.username = username; }
        public String getPassword() { return password; }
        public void setPassword(String password) { this.password = password; }
        public Imap getImap() { return imap; }
        public void setImap(Imap imap) { this.imap = imap; }
        public Smtp getSmtp() { return smtp; }
        public void setSmtp(Smtp smtp) { this.smtp = smtp; }
    }

    public static class Imap {
        @NotBlank private String host;
        @Min(1) private int port = 993;
        private boolean ssl = true;
        @NotBlank private String folder = "INBOX";
        private boolean receiverEnabled = true;

        public String getHost() { return host; }
        public void setHost(String host) { this.host = host; }
        public int getPort() { return port; }
        public void setPort(int port) { this.port = port; }
        public boolean isSsl() { return ssl; }
        public void setSsl(boolean ssl) { this.ssl = ssl; }
        public String getFolder() { return folder; }
        public void setFolder(String folder) { this.folder = folder; }
        public boolean isReceiverEnabled() { return receiverEnabled; }
        public void setReceiverEnabled(boolean receiverEnabled) { this.receiverEnabled = receiverEnabled; }
    }

    public static class Smtp {
        @NotBlank private String host;
        @Min(1) private int port = 587;
        private boolean ssl;
        private boolean starttls = true;
        @NotBlank private String authMechanisms = "LOGIN";

        public String getHost() { return host; }
        public void setHost(String host) { this.host = host; }
        public int getPort() { return port; }
        public void setPort(int port) { this.port = port; }
        public boolean isSsl() { return ssl; }
        public void setSsl(boolean ssl) { this.ssl = ssl; }
        public boolean isStarttls() { return starttls; }
        public void setStarttls(boolean starttls) { this.starttls = starttls; }
        public String getAuthMechanisms() { return authMechanisms; }
        public void setAuthMechanisms(String authMechanisms) { this.authMechanisms = authMechanisms; }
    }
}
