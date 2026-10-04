// Build for the execution node (currently Linux ARM64); optional PORT defaults to 3000.
package main

import (
    "encoding/json"
    "log"
    "net"
    "net/http"
    "os"
    "strconv"
)

func main() {
    portValue := os.Getenv("PORT")
    if portValue == "" { portValue = "3000" }
    port, err := strconv.Atoi(portValue)
    if err != nil || port < 1 || port > 65535 { log.Fatal("Invalid PORT") }
    host := "127.0.0.1"
    http.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
        w.Header().Set("Content-Type", "application/json")
        json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
    })
    http.HandleFunc("/api/analyze", func(w http.ResponseWriter, r *http.Request) {
        w.Header().Set("Content-Type", "application/json")
        if r.Method != "POST" { w.WriteHeader(405); return }
        r.Body = http.MaxBytesReader(w, r.Body, 1024 * 1024)
        var data struct { Values []float64 `json:"values"` }
        if json.NewDecoder(r.Body).Decode(&data) != nil { w.WriteHeader(400); return }
        sum := 0.0
        for _, value := range data.Values { sum += value }
        json.NewEncoder(w).Encode(map[string]float64{"sum": sum})
    })
    log.Fatal(http.ListenAndServe(net.JoinHostPort(host, strconv.Itoa(port)), nil))
}
