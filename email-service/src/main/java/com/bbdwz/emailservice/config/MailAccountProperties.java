package com.bbdwz.emailservice.config;

import jakarta.validation.Valid;
import jakarta.validation.constraints.Email;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "mail")
public class MailAccountProperties {

    @NotBlank
    private String provider = "imap-smtp";

    @Valid
    private Account account = new Account();

    @Valid
    private Imap imap = new Imap();

    @Valid
    private Smtp smtp = new Smtp();

    @Valid
    private Receiver receiver = new Receiver();

    public String getProvider() {
        return provider;
    }

    public void setProvider(String provider) {
        this.provider = provider;
    }

    public Account getAccount() {
        return account;
    }

    public void setAccount(Account account) {
        this.account = account;
    }

    public Imap getImap() {
        return imap;
    }

    public void setImap(Imap imap) {
        this.imap = imap;
    }

    public Smtp getSmtp() {
        return smtp;
    }

    public void setSmtp(Smtp smtp) {
        this.smtp = smtp;
    }

    public Receiver getReceiver() {
        return receiver;
    }

    public void setReceiver(Receiver receiver) {
        this.receiver = receiver;
    }

    public static class Account {
        @Email
        @NotBlank
        private String address;

        @NotBlank
        private String username;

        @NotBlank
        private String password;

        public String getAddress() {
            return address;
        }

        public void setAddress(String address) {
            this.address = address;
        }

        public String getUsername() {
            return username;
        }

        public void setUsername(String username) {
            this.username = username;
        }

        public String getPassword() {
            return password;
        }

        public void setPassword(String password) {
            this.password = password;
        }
    }

    public static class Imap {
        @NotBlank
        private String host;

        @Min(1)
        private int port = 993;

        private boolean ssl = true;

        @NotBlank
        private String folder = "INBOX";

        public String getHost() {
            return host;
        }

        public void setHost(String host) {
            this.host = host;
        }

        public int getPort() {
            return port;
        }

        public void setPort(int port) {
            this.port = port;
        }

        public boolean isSsl() {
            return ssl;
        }

        public void setSsl(boolean ssl) {
            this.ssl = ssl;
        }

        public String getFolder() {
            return folder;
        }

        public void setFolder(String folder) {
            this.folder = folder;
        }
    }

    public static class Smtp {
        @NotBlank
        private String host;

        @Min(1)
        private int port = 587;

        private boolean ssl;

        private boolean starttls = true;

        @NotBlank
        private String authMechanisms = "LOGIN";

        public String getHost() {
            return host;
        }

        public void setHost(String host) {
            this.host = host;
        }

        public int getPort() {
            return port;
        }

        public void setPort(int port) {
            this.port = port;
        }

        public boolean isStarttls() {
            return starttls;
        }

        public void setStarttls(boolean starttls) {
            this.starttls = starttls;
        }

        public boolean isSsl() {
            return ssl;
        }

        public void setSsl(boolean ssl) {
            this.ssl = ssl;
        }

        public String getAuthMechanisms() {
            return authMechanisms;
        }

        public void setAuthMechanisms(String authMechanisms) {
            this.authMechanisms = authMechanisms;
        }
    }

    public static class Receiver {
        private boolean enabled = true;

        public boolean isEnabled() {
            return enabled;
        }

        public void setEnabled(boolean enabled) {
            this.enabled = enabled;
        }
    }
}
