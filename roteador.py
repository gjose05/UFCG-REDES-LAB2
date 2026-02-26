# -*- coding: utf-8 -*-

import csv
import json
import threading
import time
from argparse import ArgumentParser

import requests
from flask import Flask, jsonify, request

INFINITY = 16  # Padrão RIP


class Router:
    """
    Representa um roteador que executa o algoritmo de Vetor de Distância.
    """

    def __init__(self, my_address, neighbors, my_network, update_interval=1):
        self.my_address = my_address
        self.neighbors = neighbors
        self.my_network = my_network
        self.update_interval = update_interval

        # se passar mais desse tempo sem mandar atualização o processo eh morto pra n quebrar a aplicação
        self.route_timeout = 90  

        print("DEBUG: inicializando roteador...")
        print("DEBUG network:", self.my_network)
        print("DEBUG neighbors:", self.neighbors)

        # ===============================
        # INICIALIZAÇÃO DA TABELA
        # ===============================
        self.routing_table = {}

        self.routing_table[self.my_network] = {
            'cost': 0,
            'next_hop': self.my_address,
            'last_update': time.time()
        }

        for neighbor, cost in self.neighbors.items():
            self.routing_table[neighbor] = {
                'cost': cost,
                'next_hop': neighbor,
                'last_update': time.time()
            }

        print("Tabela de roteamento inicial:")
        print(json.dumps(self.routing_table, indent=4))

        self._start_periodic_updates()

        # a checagem de tempo sem enviar nada
        self._start_timeout_checker()

    # isso aq eh pra matar o processo quando ficar muito tempo ser dar retorno e n quebrar a aplicação

    def _start_timeout_checker(self):
        thread = threading.Thread(target=self._timeout_loop)
        thread.daemon = True
        thread.start()

    def _timeout_loop(self):
        while True:
            time.sleep(5)
            now = time.time()

            for network, route in list(self.routing_table.items()):

                if network == self.my_network:
                    continue

                if now - route.get('last_update', now) > self.route_timeout:
                    if route['cost'] != INFINITY:
                        print(f"Rota para {network} expirou. Marcando como INFINITY.")
                        self.routing_table[network]['cost'] = INFINITY


    def _start_periodic_updates(self):
        thread = threading.Thread(target=self._periodic_update_loop)
        thread.daemon = True
        thread.start()

    def _periodic_update_loop(self):
        while True:
            time.sleep(self.update_interval)
            print(f"[{time.ctime()}] Enviando atualizações periódicas...")
            try:
                self.send_updates_to_neighbors()
            except Exception as e:
                print(f"Erro durante atualização periódica: {e}")

    def send_updates_to_neighbors(self):

        tabela_sumarizada = self.summarize_routes()

        for neighbor_address in self.neighbors:

            tabela_para_enviar = {}

            for network, info in tabela_sumarizada.items():

                # 3️⃣ SPLIT HORIZON
                if info['next_hop'] == neighbor_address:
                    continue

                tabela_para_enviar[network] = {
                    'cost': info['cost'],
                    'next_hop': info['next_hop']
                }

            payload = {
                "sender_address": self.my_address,
                "routing_table": tabela_para_enviar
            }

            url = f'http://{neighbor_address}/receive_update'

            try:
                print(f"Enviando tabela sumarizada para {neighbor_address}")
                requests.post(url, json=payload, timeout=5)
            except requests.exceptions.RequestException as e:
                print(f"Erro ao conectar com {neighbor_address}: {e}")


    def ip_to_int(self, ip):
        parts = ip.split('.')
        return (int(parts[0]) << 24) | \
            (int(parts[1]) << 16) | \
            (int(parts[2]) << 8)  | \
                int(parts[3])
    
    def int_to_ip(self, value):
        return ".".join([
            str((value >> 24) & 0xFF),
            str((value >> 16) & 0xFF),
            str((value >> 8) & 0xFF),
            str(value & 0xFF)
        ])
    
    def prefix_to_mask(self, prefix):
        return (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    

    def split_network(self, network):
        ip, prefix = network.split('/')
        return ip, int(prefix)

    def can_merge(self, net1, net2):
        ip1, p1 = self.split_network(net1)
        ip2, p2 = self.split_network(net2)

        if p1 != p2:
            return False

        size = 2 ** (32 - p1)

        int1 = self.ip_to_int(ip1)
        int2 = self.ip_to_int(ip2)

        return abs(int1 - int2) == size

    def merge_networks(self, net1, net2):
        ip1, p1 = self.split_network(net1)
        ip2, p2 = self.split_network(net2)

        new_prefix = p1 - 1
        mask = self.prefix_to_mask(new_prefix)

        int1 = self.ip_to_int(ip1)
        new_network_int = int1 & mask

        return f"{self.int_to_ip(new_network_int)}/{new_prefix}"
    
    def common_prefix(self, net1, net2):
        ip1, _ = self.split_network(net1)
        ip2, _ = self.split_network(net2)

        int1 = self.ip_to_int(ip1)
        int2 = self.ip_to_int(ip2)

        xor = int1 ^ int2

        prefix = 0
        for i in range(31, -1, -1):
            if (xor >> i) & 1:
                break
            prefix += 1

        return prefix
    
    def summarize_routes(self):

        grouped = {}

        # 1️⃣ Agrupar por next_hop (exceto minha própria rede)
        for network, info in self.routing_table.items():
            if network == self.my_network:
                continue

            next_hop = info['next_hop']
            grouped.setdefault(next_hop, []).append((network, info['cost']))

        summarized = {}

        # 2️⃣ Processar cada grupo
        for next_hop, routes in grouped.items():

            # Se só tiver uma rota → mantém
            if len(routes) == 1:
                net, cost = routes[0]
                summarized[net] = {
                    'cost': cost,
                    'next_hop': next_hop,
                    'last_update': time.time()
                }
                continue

            # 3️⃣ Converter IPs para inteiro
            network_ints = []
            costs = []

            for net, cost in routes:
                ip, prefix = self.split_network(net)
                network_ints.append(self.ip_to_int(ip))
                costs.append(cost)

            min_ip = min(network_ints)
            max_ip = max(network_ints)

            # 4️⃣ Descobrir prefixo comum via XOR
            xor = min_ip ^ max_ip

            prefix = 0
            for i in range(31, -1, -1):
                if (xor >> i) & 1:
                    break
                prefix += 1

            # ⚠ Evitar sumarização perigosa
            if prefix < 16:
                # mantém rotas originais
                for net, cost in routes:
                    summarized[net] = {
                        'cost': cost,
                        'next_hop': next_hop,
                        'last_update': time.time()
                    }
                continue

            # 5️⃣ Criar máscara e super-rede
            mask = self.prefix_to_mask(prefix)
            supernet_int = min_ip & mask
            supernet = f"{self.int_to_ip(supernet_int)}/{prefix}"

            # 6️⃣ Verificar se TODAS redes pertencem à super-rede
            valid = True
            for ip_int in network_ints:
                if (ip_int & mask) != supernet_int:
                    valid = False
                    break

            if not valid:
                # mantém originais
                for net, cost in routes:
                    summarized[net] = {
                        'cost': cost,
                        'next_hop': next_hop,
                        'last_update': time.time()
                    }
                continue

            # 7️⃣ Criar rota sumarizada
            summarized[supernet] = {
                'cost': max(costs),  # regra obrigatória
                'next_hop': next_hop,
                'last_update': time.time()
            }

        # 8️⃣ Garantir que minha rede sempre está presente
        summarized[self.my_network] = self.routing_table[self.my_network]

        return summarized
# ===============================
# FLASK SETUP
# ===============================

app = Flask(__name__)
router_instance = None


@app.route('/routes', methods=['GET'])
def get_routes():
    if router_instance:
        return jsonify({
            "message": "Tabela atual",
            "vizinhos": router_instance.neighbors,
            "my_network": router_instance.my_network,
            "my_address": router_instance.my_address,
            "update_interval": router_instance.update_interval,
            "routing_table": router_instance.routing_table
        })
    return jsonify({"error": "Roteador não inicializado"}), 500


@app.route('/receive_update', methods=['POST'])
def receive_update():
    if not request.json:
        return jsonify({"error": "Invalid request"}), 400

    update_data = request.json
    sender_address = update_data.get("sender_address")
    sender_table = update_data.get("routing_table")

    if not sender_address or not isinstance(sender_table, dict):
        return jsonify({"error": "Missing sender_address or routing_table"}), 400

    print(f"Atualização recebida de {sender_address}")
    print(json.dumps(sender_table, indent=4))

    if sender_address not in router_instance.neighbors:
        return jsonify({"status": "ignored"}), 200

    cost_to_neighbor = router_instance.neighbors[sender_address]
    table_changed = False

    for network, info in sender_table.items():

        advertised_cost = info['cost']
        new_cost = cost_to_neighbor + advertised_cost

        if new_cost > INFINITY:
            new_cost = INFINITY

        current_route = router_instance.routing_table.get(network)

        # Caso A: nova rota
        if current_route is None:
            if new_cost < INFINITY:
                router_instance.routing_table[network] = {
                    'cost': new_cost,
                    'next_hop': sender_address,
                    'last_update': time.time()  
                }
                table_changed = True

        # Caso B: rota melhor
        elif new_cost < current_route['cost']:
            router_instance.routing_table[network] = {
                'cost': new_cost,
                'next_hop': sender_address,
                'last_update': time.time()  
            }
            table_changed = True

        # Caso C: mesmo next_hop (propagação de falha)
        elif current_route['next_hop'] == sender_address:
            if current_route['cost'] != new_cost:
                router_instance.routing_table[network]['cost'] = new_cost
                router_instance.routing_table[network]['last_update'] = time.time()  
                table_changed = True

    if table_changed:
        print("Tabela atualizada:")
        print(json.dumps(router_instance.routing_table, indent=4))

    return jsonify({"status": "success"}), 200


# ===============================
# MAIN
# ===============================

if __name__ == '__main__':
    parser = ArgumentParser(description="Simulador de Roteador com Vetor de Distância")
    parser.add_argument('-p', '--port', type=int, default=5000)
    parser.add_argument('-f', '--file', type=str, required=True)
    parser.add_argument('--network', type=str, required=True)
    parser.add_argument('--interval', type=int, default=5)
    args = parser.parse_args()

    neighbors_config = {}
    try:
        with open(args.file, mode='r') as infile:
            reader = csv.DictReader(infile)
            for row in reader:
                neighbors_config[row['vizinho']] = int(row['custo'])
    except Exception as e:
        print(f"Erro ao ler CSV: {e}")
        exit(1)

    my_full_address = f"127.0.0.1:{args.port}"

    print("--- Iniciando Roteador ---")
    print(f"Endereço: {my_full_address}")
    print(f"Rede Local: {args.network}")
    print(f"Vizinhos: {neighbors_config}")
    print("--------------------------")

    router_instance = Router(
        my_address=my_full_address,
        neighbors=neighbors_config,
        my_network=args.network,
        update_interval=args.interval
    )

    app.run(host='0.0.0.0', port=args.port, debug=False)
