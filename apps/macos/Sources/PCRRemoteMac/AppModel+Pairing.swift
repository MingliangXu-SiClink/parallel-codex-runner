import Foundation

extension AppModel {
    func monitorMobilePairing() async {
        await prepareMobilePairing()
        while pairingPresented,
              relay.pairingUri?.isEmpty == false,
              !Task.isCancelled {
            do {
                try await Task.sleep(for: .seconds(2))
            } catch {
                return
            }
            await pollRelayPairing()
        }
    }

    func prepareMobilePairing() async {
        pairingError = nil
        if !daemonOnline {
            await refresh(reportErrors: false)
        }
        guard daemonOnline else {
            pairingError = appLanguage.text(
                "服务端不可用，请重新打开 Parallel Codex 后重试。",
                "The server is unavailable. Reopen Parallel Codex and try again."
            )
            return
        }
        if relay.hasConnectedPhone || pairingCodeIsFresh {
            return
        }
        await createMobilePairing(force: true)
    }

    private var pairingCodeIsFresh: Bool {
        guard relay.pairingUri?.isEmpty == false,
              let rawExpiry = relay.pairingExpiresAt,
              let expiry = parsePairingDate(rawExpiry) else {
            return false
        }
        return expiry.timeIntervalSinceNow > 10
    }

    private func parsePairingDate(_ value: String) -> Date? {
        parseInternetDate(value)
    }

    func createMobilePairing(force: Bool = false) async {
        guard !pairingIsLoading else { return }
        let target = client.currentTarget()
        pairingIsLoading = true
        pairingError = nil
        defer { pairingIsLoading = false }
        do {
            let status: RelayStatus = try await client.call(
                target: target,
                "relay.pairing_create",
                body: RelayPairingBody(force: force)
            )
            if isCurrentMachine(target.machineID) { relay = status }
        } catch {
            if isCurrentMachine(target.machineID) {
                pairingError = appLanguage.userMessage(for: error)
            }
        }
    }

    func pollRelayPairing() async {
        let target = client.currentTarget()
        do {
            let result: RelayPollResult = try await client.call(
                target: target,
                "relay.poll",
                body: EmptyBody()
            )
            guard isCurrentMachine(target.machineID) else { return }
            if result.pairingStatus == "approved" || result.pairingStatus == "claimed" {
                await refresh()
            } else if result.pairingStatus == "expired" {
                await createMobilePairing(force: true)
                return
            }
            pairingError = nil
        } catch {
            if isCurrentMachine(target.machineID) {
                pairingError = appLanguage.userMessage(for: error)
            }
        }
    }

}
